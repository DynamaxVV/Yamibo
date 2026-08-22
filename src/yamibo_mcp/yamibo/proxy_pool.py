from __future__ import annotations

import json
import copy
import logging
import re
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import dataclass, field
from collections.abc import Iterator

from yamibo_mcp.config import Settings


@dataclass(frozen=True)
class MihomoProxyPoolConfig:
    enabled: bool
    controller_url: str
    secret: str
    proxy_url: str
    selector_group: str
    test_url: str
    test_timeout_ms: int
    failure_policy: str
    allowed_patterns: tuple[str, ...] = field(default=())
    denied_patterns: tuple[str, ...] = field(default=())
    max_delay_ms: int = 0

    @classmethod
    def from_config_section(cls, section: dict | None) -> MihomoProxyPoolConfig:
        if isinstance(section, dict):
            return cls(
                enabled=str(section.get("enabled", False)).lower() in {"1", "true", "yes", "on"},
                controller_url=str(section.get("controller_url", "")),
                secret=str(section.get("secret", "")),
                proxy_url=str(section.get("proxy_url", "")),
                selector_group=str(section.get("selector_group", "")),
                test_url=str(section.get("test_url", "https://www.gstatic.com/generate_204")),
                test_timeout_ms=int(section.get("test_timeout_ms", 3000)),
                failure_policy=str(section.get("failure_policy", "fail_open")),
                allowed_patterns=tuple(str(p) for p in section.get("allowed_patterns", []) if isinstance(p, str) and p.strip()),
                denied_patterns=tuple(str(p) for p in section.get("denied_patterns", []) if isinstance(p, str) and p.strip()),
                max_delay_ms=int(section.get("max_delay_ms", 0)),
            )
        return cls(
            enabled=False,
            controller_url="",
            secret="",
            proxy_url="",
            selector_group="",
            test_url="https://www.gstatic.com/generate_204",
            test_timeout_ms=3000,
            failure_policy="fail_open",
        )

LOG = logging.getLogger(__name__)
YAMIBO_HEALTH_CHECK_URL = "https://bbs.yamibo.com/"
NETWORK_HEALTH_CHECK_URL = "https://www.gstatic.com/generate_204"
DIRECT_NODE_NAME = "DIRECT"

# --- cache ---

_CACHE_TTL_SECONDS = 30.0
_cache: dict[tuple[str, str], tuple[list[tuple[str, int]], float]] = {}
_cache_lock = threading.Lock()
_HEALTH_CACHE_TTL_SECONDS = 60.0
_health_cache: dict[tuple[str, str, str], tuple[dict, float]] = {}
_selector_locks: dict[tuple[str, str], threading.Lock] = {}
_selector_locks_lock = threading.Lock()


def _cache_key(controller_url: str, selector_group: str) -> tuple[str, str]:
    return (controller_url.rstrip("/"), selector_group)


def _cache_get(key: tuple[str, str]) -> list[tuple[str, int]] | None:
    with _cache_lock:
        entry = _cache.get(key)
        if entry is None:
            return None
        nodes, ts = entry
        if time.monotonic() - ts > _CACHE_TTL_SECONDS:
            del _cache[key]
            return None
        return list(nodes)


def _cache_set(key: tuple[str, str], nodes: list[tuple[str, int]]) -> None:
    with _cache_lock:
        _cache[key] = (list(nodes), time.monotonic())


def clear_proxy_cache() -> int:
    """Clear the proxy pool delay cache. Returns count of entries removed."""
    with _cache_lock:
        count = len(_cache)
        _cache.clear()
        _health_cache.clear()
        return count


def _selector_lock_for(key: tuple[str, str]) -> threading.Lock:
    with _selector_locks_lock:
        lock = _selector_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _selector_locks[key] = lock
        return lock


# --- 节点级 444 黑名单 ---
# 单个节点被 444 后临时拉黑（TTL 到期自动恢复），避免反复撞同一节点。
# 全部可用节点都在黑名单时，由 anti_bot.handle_http_444 触发全局暂停。
# daemon 单 worker 串行处理 job，以下 dict 无需额外加锁。

_NODE_BLACKLIST_TTL_SECONDS = 300.0  # 5 分钟
_node_blacklist: dict[str, float] = {}  # node -> 过期 monotonic 时间戳
_current_node_by_job: dict[str, str] = {}  # job_id -> 最近选定的 node
_retry_hint_by_job: dict[str, int] = {}  # job_id -> 444 重试计数


def _prune_blacklist() -> None:
    """删除已过期的黑名单条目。"""
    now = time.monotonic()
    expired = [n for n, exp in _node_blacklist.items() if exp <= now]
    for n in expired:
        del _node_blacklist[n]


def mark_node_444(node: str | None) -> None:
    """把节点加入 444 黑名单。node 为 None 时无操作。"""
    if not node:
        return
    _node_blacklist[node] = time.monotonic() + _NODE_BLACKLIST_TTL_SECONDS
    LOG.info("mihomo: node %s blacklisted for %.0fs after HTTP 444", node, _NODE_BLACKLIST_TTL_SECONDS)


def clear_node_blacklist() -> int:
    """清空节点黑名单，返回清除条目数。测试用。"""
    count = len(_node_blacklist)
    _node_blacklist.clear()
    return count


def get_blacklisted_nodes() -> list[str]:
    """返回当前黑名单中的节点名（已剪掉过期项）。"""
    _prune_blacklist()
    return list(_node_blacklist.keys())


def all_nodes_blacklisted(settings) -> bool:
    """是否所有可用节点都在黑名单中（即没有可用节点了）。

    重新发现节点 + delay test（绕过缓存）以拿到当前可用集合，再与黑名单求差。
    无可用节点配置时返回 False（让全局阈值逻辑兜底）。
    """
    if not _check_enabled(settings):
        return False
    _prune_blacklist()
    if not _node_blacklist:
        return False
    cfg = settings.proxy_pool
    client = MihomoControllerClient(
        controller_url=cfg.controller_url,
        secret=cfg.secret,
        selector_group=cfg.selector_group,
        test_url=cfg.test_url,
        test_timeout_ms=cfg.test_timeout_ms,
    )
    nodes = client.discover_nodes()
    if not nodes:
        return False
    usable = client.test_all_nodes_parallel(nodes)
    usable = _filter_nodes(
        usable,
        allowed_patterns=cfg.allowed_patterns,
        denied_patterns=cfg.denied_patterns,
        max_delay_ms=cfg.max_delay_ms,
    )
    if not usable:
        return True  # 没有可用节点 ≡ 全黑名单
    usable_names = {name for name, _ in usable}
    return usable_names.issubset(_node_blacklist.keys())


def record_job_node(job_id: str | None, node: str | None) -> None:
    """记录某 job 当前选定的 node，供 444 时反查。"""
    if job_id is None:
        return
    if node is None:
        _current_node_by_job.pop(job_id, None)
    else:
        _current_node_by_job[job_id] = node


def get_job_node(job_id: str | None) -> str | None:
    """取某 job 最近选定的 node。"""
    if job_id is None:
        return None
    return _current_node_by_job.get(job_id)


def bump_retry_hint(job_id: str | None) -> int:
    """自增并返回某 job 的 444 重试计数。"""
    if job_id is None:
        return 0
    current = _retry_hint_by_job.get(job_id, 0) + 1
    _retry_hint_by_job[job_id] = current
    return current


def get_retry_hint(job_id: str | None) -> int:
    """取某 job 的 444 重试计数（默认 0）。"""
    if job_id is None:
        return 0
    return _retry_hint_by_job.get(job_id, 0)


def clear_job_state(job_id: str | None) -> None:
    """清掉某 job 的 node/retry_hint 状态。job 终态时调用。"""
    if job_id is None:
        return
    _current_node_by_job.pop(job_id, None)
    _retry_hint_by_job.pop(job_id, None)



def _filter_nodes(
    nodes: list[tuple[str, int]],
    *,
    allowed_patterns: tuple[str, ...] = (),
    denied_patterns: tuple[str, ...] = (),
    max_delay_ms: int = 0,
) -> list[tuple[str, int]]:
    """Filter node list by allow/deny patterns and max delay.

    Rules are applied in order:
    1. allowed_patterns: if non-empty, node must match at least one (regex)
    2. denied_patterns: node matching any is excluded (regex)
    3. max_delay_ms: nodes slower than this are excluded (0 = no limit)
    """
    if allowed_patterns:
        allowed_re = [re.compile(p) for p in allowed_patterns]
        nodes = [(name, d) for name, d in nodes if any(r.search(name) for r in allowed_re)]
        if not nodes:
            LOG.warning("mihomo: no nodes match allowed_patterns=%s", allowed_patterns)

    if denied_patterns:
        denied_re = [re.compile(p) for p in denied_patterns]
        before = len(nodes)
        nodes = [(name, d) for name, d in nodes if not any(r.search(name) for r in denied_re)]
        if len(nodes) < before:
            LOG.info("mihomo: denied %d nodes matching denied_patterns=%s", before - len(nodes), denied_patterns)

    if max_delay_ms > 0:
        before = len(nodes)
        nodes = [(name, d) for name, d in nodes if d <= max_delay_ms]
        if len(nodes) < before:
            LOG.info("mihomo: filtered %d nodes exceeding max_delay_ms=%d", before - len(nodes), max_delay_ms)

    return nodes


# --- dataclasses ---


@dataclass(frozen=True)
class ProxyBinding:
    group: str
    node: str
    proxy_url: str
    best_effort: bool
    diagnostics: dict


# --- controller client ---


class MihomoControllerClient:
    def __init__(
        self,
        *,
        controller_url: str,
        secret: str,
        selector_group: str,
        test_url: str,
        test_timeout_ms: int,
    ) -> None:
        self._controller_url = controller_url.rstrip("/")
        self._secret = secret
        self._selector_group = selector_group
        self._test_url = test_url
        self._test_timeout_ms = test_timeout_ms
        # The controller is a local control-plane endpoint.  Do not inherit
        # HTTP(S)_PROXY here: in this setup 7890 is Mihomo's traffic proxy,
        # and sending the controller request through it turns a healthy
        # 127.0.0.1:9097 API call into HTTP 502.
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def _request(self, path: str, method: str = "GET", body: bytes | None = None, timeout: float | None = None) -> dict:
        url = f"{self._controller_url}{path}"
        req = urllib.request.Request(url, data=body, method=method)
        if self._secret:
            req.add_header("Authorization", f"Bearer {self._secret}")
        if body is not None:
            req.add_header("Content-Type", "application/json")
        # 默认时间 = 发现/选择用充裕时间，delay test 由调用方传短超时。
        timeout = timeout if timeout is not None else max(10, (self._test_timeout_ms / 1000) + 3)
        with self._opener.open(req, timeout=timeout) as resp:
            raw = resp.read()
            if not raw:
                return {}
            return json.loads(raw.decode("utf-8"))

    def discover_nodes(self) -> list[str]:
        try:
            data = self._request("/proxies")
        except Exception:
            LOG.warning("mihomo: failed to fetch /proxies", exc_info=True)
            return []
        group_data = data.get("proxies", {}).get(self._selector_group)
        if not group_data:
            return []
        return list(group_data.get("all", []))

    def test_node_delay(self, node: str, *, test_url: str | None = None) -> int | None:
        import urllib.parse as _up

        params = _up.urlencode(
            {"url": test_url or self._test_url, "timeout": str(self._test_timeout_ms)}
        )
        path = f"/proxies/{_up.quote(node)}/delay?{params}"
        # delay test 用短超时：test_timeout_ms 加 2s 缓冲，至少 3s 而不是 10s。
        delay_timeout = max(3.0, (self._test_timeout_ms / 1000) + 2.0)
        try:
            data = self._request(path, timeout=delay_timeout)
        except urllib.error.HTTPError as exc:
            # 5xx = 控制器透过该节点代理失败（正常，节点不可达）
            if exc.code is not None and exc.code >= 500:
                LOG.debug("mihomo: node %s delay test returned %d — skipping", node, exc.code)
            else:
                LOG.info("mihomo: node %s delay test HTTP %s", node, exc.code)
            return None
        except Exception:
            LOG.info("mihomo: delay test failed for node %s", node)
            return None
        delay = data.get("delay")
        if isinstance(delay, (int, float)):
            return int(delay)
        return None

    def test_all_nodes_parallel(
        self,
        nodes: list[str],
        max_workers: int = 8,
        *,
        test_url: str | None = None,
    ) -> list[tuple[str, int]]:
        """Test delay of all nodes in parallel, returning (node, delay_ms) for usable nodes.

        Stops waiting once at least one node passes AND deadline is reached,
        so a few bad nodes don't stall callers for the full per-node timeout.
        """
        if not nodes:
            return []

        deadline = time.monotonic() + max(5.0, min(15.0, len(nodes) * 0.8))

        results: list[tuple[str, int]] = []
        with ThreadPoolExecutor(max_workers=min(max_workers, len(nodes))) as executor:
            if test_url is None:
                future_to_node = {
                    executor.submit(self.test_node_delay, node): node
                    for node in nodes
                }
            else:
                future_to_node = {
                    executor.submit(self.test_node_delay, node, test_url=test_url): node
                    for node in nodes
                }
            for future in as_completed(future_to_node):
                node = future_to_node[future]
                try:
                    delay = future.result(timeout=1.0)
                    if delay is not None:
                        results.append((node, delay))
                except Exception:
                    pass
                # 有结果且已过截止时间 → 不等剩余慢节点
                if results and time.monotonic() > deadline:
                    for f in future_to_node:
                        f.cancel()
                    break

        return sorted(results, key=lambda x: x[1])

    def select_node(self, node: str) -> None:
        body = json.dumps({"name": node}).encode("utf-8")
        self._request(f"/proxies/{self._selector_group}", method="PUT", body=body)


# --- public API ---


def _check_enabled(settings) -> bool:
    """Safely check proxy_pool.enabled — resilient to MagicMock."""
    cfg = getattr(settings, "proxy_pool", None)
    if cfg is None:
        return False
    if not isinstance(cfg, MihomoProxyPoolConfig):
        return False
    return cfg.enabled


def select_thread_proxy(
    settings: Settings,
    tid: int,
    job_id: str | None = None,
    retry_hint: int | None = None,
) -> ProxyBinding | None:
    """选择线程代理节点。

    retry_hint 为 None 时，自动从 _retry_hint_by_job 取（444 重试时 runner 会
    bump 该计数），让同一 tid 在重试时换到不同节点。
    """
    if not _check_enabled(settings):
        return None

    if retry_hint is None:
        retry_hint = get_retry_hint(job_id)
    cfg = settings.proxy_pool
    key = _cache_key(cfg.controller_url, cfg.selector_group)
    cached = _cache_get(key)
    if cached is not None:
        usable = cached
        from_cache = True
    else:
        client = MihomoControllerClient(
            controller_url=cfg.controller_url,
            secret=cfg.secret,
            selector_group=cfg.selector_group,
            test_url=cfg.test_url,
            test_timeout_ms=cfg.test_timeout_ms,
        )

        nodes = client.discover_nodes()
        if not nodes:
            LOG.warning("mihomo: no nodes discovered for group %s", cfg.selector_group)
            return None

        usable = client.test_all_nodes_parallel(nodes)
        if not usable:
            LOG.warning("mihomo: no usable nodes after delay tests")
            return None

        usable = _filter_nodes(
            usable,
            allowed_patterns=cfg.allowed_patterns,
            denied_patterns=cfg.denied_patterns,
            max_delay_ms=cfg.max_delay_ms,
        )
        if not usable:
            LOG.warning("mihomo: no usable nodes after filters")
            return None

        _cache_set(key, usable)
        from_cache = False

    # 过滤 444 黑名单节点
    _prune_blacklist()
    if _node_blacklist:
        usable = [(n, d) for n, d in usable if n not in _node_blacklist]
        if not usable:
            LOG.warning("mihomo: all usable nodes blacklisted after 444 filtering for job %s", job_id)
            record_job_node(job_id, None)
            return None

    idx = hash((tid, retry_hint)) % len(usable)
    selected_node, selected_delay = usable[idx]
    record_job_node(job_id, selected_node)

    client = MihomoControllerClient(
        controller_url=cfg.controller_url,
        secret=cfg.secret,
        selector_group=cfg.selector_group,
        test_url=cfg.test_url,
        test_timeout_ms=cfg.test_timeout_ms,
    )
    try:
        with _selector_lock_for(key):
            client.select_node(selected_node)
    except Exception:
        LOG.warning("mihomo: failed to select node %s", selected_node, exc_info=True)
        # Still return binding — proxy may already be on this node

    return ProxyBinding(
        group=cfg.selector_group,
        node=selected_node,
        proxy_url=cfg.proxy_url,
        best_effort=True,
        diagnostics={
            "delay_ms": selected_delay,
            "candidates": len(usable),
            "from_cache": from_cache,
            "retry_hint": retry_hint,
        },
    )


@contextmanager
def activate_proxy_binding(settings: Settings, binding: ProxyBinding | None) -> Iterator[ProxyBinding | None]:
    """Hold the Mihomo selector on one node for a job's full remote request span.

    Mihomo selectors are process-external global state. Without holding this lock,
    concurrent workers can switch the selector between pages of the same thread.
    """
    if binding is None or not _check_enabled(settings):
        yield binding
        return
    cfg = settings.proxy_pool
    key = _cache_key(cfg.controller_url, cfg.selector_group)
    lock = _selector_lock_for(key)
    lock.acquire()
    try:
        client = MihomoControllerClient(
            controller_url=cfg.controller_url,
            secret=cfg.secret,
            selector_group=cfg.selector_group,
            test_url=cfg.test_url,
            test_timeout_ms=cfg.test_timeout_ms,
        )
        try:
            client.select_node(binding.node)
        except Exception:
            LOG.warning("mihomo: failed to activate held node %s", binding.node, exc_info=True)
        yield binding
    finally:
        lock.release()


def check_proxy_pool_health(settings: Settings, *, test_url: str | None = None) -> dict:
    """Diagnostic check for mihomo proxy pool connectivity.

    Always performs fresh discovery + delay tests (bypasses cache).
    """
    cfg = getattr(settings, "proxy_pool", None)
    if cfg is None:
        return {"ok": False, "error": "proxy_pool not configured in settings"}

    report: dict = {
        "ok": True,
        "config": {
            "enabled": cfg.enabled,
            "controller_url": cfg.controller_url,
            "selector_group": cfg.selector_group,
            "proxy_url": cfg.proxy_url,
            "test_url": cfg.test_url,
            "test_timeout_ms": cfg.test_timeout_ms,
            "failure_policy": cfg.failure_policy,
        },
        "controller": {"reachable": False, "error": None},
        "selector_group": {"exists": False, "current_node": None, "node_count": 0},
        "nodes": [],
        "probe_url": test_url or cfg.test_url,
        "cache": _cache_info(),
    }

    if not cfg.enabled:
        report["ok"] = False
        report["error"] = "proxy_pool is disabled"
        return report

    probe_url = test_url or cfg.test_url
    client = MihomoControllerClient(
        controller_url=cfg.controller_url,
        secret=cfg.secret,
        selector_group=cfg.selector_group,
        test_url=probe_url,
        test_timeout_ms=cfg.test_timeout_ms,
    )

    # Step 1: controller reachable?
    try:
        data = client._request("/proxies")
        report["controller"]["reachable"] = True
    except Exception as exc:
        report["controller"]["error"] = str(exc)
        report["ok"] = False
        report["error"] = f"controller unreachable: {exc}"
        return report

    # Step 2: selector group exists?
    proxies = data.get("proxies", {})
    group_data = proxies.get(cfg.selector_group)
    if group_data is None:
        report["ok"] = False
        report["error"] = (
            f"selector group '{cfg.selector_group}' not found. "
            f"Available groups: {list(proxies.keys())}"
        )
        return report

    report["selector_group"]["exists"] = True
    report["selector_group"]["current_node"] = group_data.get("now")
    all_nodes = list(group_data.get("all", []))
    report["selector_group"]["node_count"] = len(all_nodes)

    report["filters"] = {
        "allowed_patterns": list(cfg.allowed_patterns) if cfg.allowed_patterns else None,
        "denied_patterns": list(cfg.denied_patterns) if cfg.denied_patterns else None,
        "max_delay_ms": cfg.max_delay_ms if cfg.max_delay_ms > 0 else None,
    }

    # Step 3: test all nodes against Yamibo and a neutral network endpoint.
    # A neutral endpoint distinguishes a dead node from a node that reaches
    # the Internet but is currently blocked by Yamibo.
    test_nodes = list(all_nodes)
    if DIRECT_NODE_NAME not in test_nodes:
        test_nodes.append(DIRECT_NODE_NAME)
    if test_nodes:
        usable_pairs = client.test_all_nodes_parallel(test_nodes)
        network_client = MihomoControllerClient(
            controller_url=cfg.controller_url,
            secret=cfg.secret,
            selector_group=cfg.selector_group,
            test_url=NETWORK_HEALTH_CHECK_URL,
            test_timeout_ms=cfg.test_timeout_ms,
        )
        network_pairs = network_client.test_all_nodes_parallel(test_nodes)
        filtered_pairs = _filter_nodes(
            usable_pairs,
            allowed_patterns=cfg.allowed_patterns,
            denied_patterns=cfg.denied_patterns,
            max_delay_ms=cfg.max_delay_ms,
        )
        filtered_names = {name for name, _ in filtered_pairs}
        usable_map = {node: delay for node, delay in usable_pairs}
        network_map = {node: delay for node, delay in network_pairs}
        blacklisted_names = set(get_blacklisted_nodes())
        for node in test_nodes:
            forum_delay = usable_map.get(node)
            network_delay = network_map.get(node)
            delay = forum_delay if forum_delay is not None else network_delay
            is_direct = node == DIRECT_NODE_NAME
            yamibo_accessible = forum_delay is not None
            if forum_delay is None and network_delay is not None:
                status = "forum_blocked"
            elif forum_delay is None:
                status = "unreachable"
            elif node in blacklisted_names:
                status = "blacklisted"
            elif node in filtered_names:
                status = "usable"
            else:
                status = "filtered"
            node_info: dict = {
                "name": node,
                "display_name": "本地直连" if is_direct else node,
                "delay_ms": delay,
                "forum_delay_ms": forum_delay,
                "network_delay_ms": network_delay,
                "error": None if delay is not None else "no delay data returned",
                "status": status,
                "blacklisted": node in blacklisted_names,
                "yamibo_accessible": yamibo_accessible,
                "is_direct": is_direct,
            }
            if forum_delay is not None and node not in filtered_names:
                node_info["filtered"] = True
            report["nodes"].append(node_info)
    else:
        usable_pairs = []
        filtered_pairs = []

    report["usable_count"] = len(usable_pairs)
    report["filtered_count"] = len(filtered_pairs)
    if filtered_pairs:
        delays = [d for _, d in filtered_pairs]
        report["min_delay_ms"] = min(delays)
        report["max_delay_ms"] = max(delays)

    if not filtered_pairs:
        report["ok"] = False
        report["error"] = "no usable nodes (all delay tests failed)"

    return report


def get_cached_proxy_pool_health(
    settings: Settings,
    *,
    test_url: str | None = None,
    force_refresh: bool = False,
) -> dict:
    """Return a health report without re-testing until the short TTL expires."""
    cfg = getattr(settings, "proxy_pool", None)
    if cfg is None:
        report = check_proxy_pool_health(settings, test_url=test_url)
        report.update({"cached": False, "cache_age_seconds": None})
        return report

    probe_url = test_url or cfg.test_url
    key = (cfg.controller_url.rstrip("/"), cfg.selector_group, probe_url)
    now = time.monotonic()
    if not force_refresh:
        with _cache_lock:
            entry = _health_cache.get(key)
        if entry is not None:
            cached_report, checked_at = entry
            age = now - checked_at
            if age <= _HEALTH_CACHE_TTL_SECONDS:
                report = copy.deepcopy(cached_report)
                report.update({"cached": True, "cache_age_seconds": round(age, 1)})
                return report

    report = check_proxy_pool_health(settings, test_url=probe_url)
    with _cache_lock:
        _health_cache[key] = (copy.deepcopy(report), time.monotonic())
    report.update({"cached": False, "cache_age_seconds": 0.0})
    return report


def _cache_info() -> dict:
    with _cache_lock:
        entries = {
            f"{key[0]}/{key[1]}": {
                "node_count": len(nodes),
                "age_seconds": round(time.monotonic() - ts, 1),
            }
            for key, (nodes, ts) in _cache.items()
        }
        return {"entries": len(_cache), "ttl_seconds": _CACHE_TTL_SECONDS, "groups": entries}


def select_random_proxy(settings: Settings) -> ProxyBinding | None:
    """Select a random proxy node for non-thread actions (search, forum browse).

    Uses the same cached node list as select_thread_proxy but picks randomly.
    """
    import random

    if not _check_enabled(settings):
        return None

    cfg = settings.proxy_pool

    key = _cache_key(cfg.controller_url, cfg.selector_group)
    cached = _cache_get(key)
    if cached is not None:
        usable = cached
    else:
        client = MihomoControllerClient(
            controller_url=cfg.controller_url,
            secret=cfg.secret,
            selector_group=cfg.selector_group,
            test_url=cfg.test_url,
            test_timeout_ms=cfg.test_timeout_ms,
        )
        nodes = client.discover_nodes()
        if not nodes:
            return None
        usable = client.test_all_nodes_parallel(nodes)
        usable = _filter_nodes(
            usable,
            allowed_patterns=cfg.allowed_patterns,
            denied_patterns=cfg.denied_patterns,
            max_delay_ms=cfg.max_delay_ms,
        )
        if not usable:
            return None
        _cache_set(key, usable)

    # 过滤 444 黑名单节点（非线程动作也跳过被拉黑的节点）
    _prune_blacklist()
    if _node_blacklist:
        usable = [(n, d) for n, d in usable if n not in _node_blacklist]
        if not usable:
            LOG.warning("mihomo: all usable nodes blacklisted (random select)")
            return None

    selected_node = random.choice(usable)[0]

    client = MihomoControllerClient(
        controller_url=cfg.controller_url,
        secret=cfg.secret,
        selector_group=cfg.selector_group,
        test_url=cfg.test_url,
        test_timeout_ms=cfg.test_timeout_ms,
    )
    try:
        with _selector_lock_for(key):
            client.select_node(selected_node)
    except Exception:
        LOG.warning("mihomo: failed to select node for random proxy %s", selected_node, exc_info=True)

    return ProxyBinding(
        group=cfg.selector_group,
        node=selected_node,
        proxy_url=cfg.proxy_url,
        best_effort=True,
        diagnostics={"candidates": len(usable)},
    )
