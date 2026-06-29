# 账号池与权限分配

> 版本：0.9.3 | 更新日期：2026-06-28

本文记录当前已经落地的账号池行为。目标很直接：普通抓取默认走低权限账号，只有在权限不足或需要看列表/搜索时，才切到更高权限账号。

## 1. 规则

- 每个账号都有独立 `cookie_file`。
- 每个账号都要标注 `permission_level`，数值越大权限越高。
- 推荐按 `0/10/20/...` 这种梯队配置，`0` 是最低权限，每一档增加 10。
- 默认选择最低可用权限账号。
- 列表、搜索、帖子预览优先选择更高权限账号。
- 帖子抓取或更新遇到“阅读权限高于 xx 才能浏览”时，自动切到更高一档的账号重试。
- 配置了 `cookie_file` 覆盖时，仍然直接使用该文件。

## 2. 配置

`yamibo.account_pool` 由若干账号组成：

```json
{
  "yamibo": {
    "account_pool": [
      {
        "account_id": "low",
        "username": "user_low",
        "password": "pass_low",
        "cookie_file": "data/cookies/low.cookie",
        "enabled": true,
        "weight": 1,
        "permission_level": 0,
        "request_interval_seconds": 1.0,
        "request_interval_jitter_seconds": 0.5,
        "max_concurrent_leases": 1,
        "login_mode": "refresh_on_login_required"
      },
      {
        "account_id": "high",
        "username": "user_high",
        "password": "pass_high",
        "cookie_file": "data/cookies/high.cookie",
        "enabled": true,
        "weight": 1,
        "permission_level": 10,
        "request_interval_seconds": 1.0,
        "request_interval_jitter_seconds": 0.5,
        "max_concurrent_leases": 1,
        "login_mode": "refresh_on_login_required"
      }
    ]
  }
}
```

兼容规则：

- 未配置 `account_pool` 时，继续使用单账号字段：
  - `yamibo.cookie_file`
  - `login.username`
  - `login.password`
- `permission_level` 默认为 `0`，不写也能跑，但文档和配置最好显式标出来。

## 3. 选择逻辑

`AccountPool.acquire()` 现在只做三件事：

1. 过滤 `enabled` 账号。
2. 按 `min_permission` 过滤可用账号。
3. 在可用账号里按权限和当前占用数选一个。

默认模式下优先低权限账号；`prefer_high_permission=True` 时反过来优先高权限账号。

当页面提示“阅读权限高于 xx 才能浏览”时，系统会按 `xx + 1` 重新选择账号，避免再次落回同一档权限。

## 4. 接入点

当前已经接入：

- `application/remote_queries.py` 的 `browse_forum_page`、`search_threads`、`inspect_remote_thread`
- `application/update_queries.py` 的 `check_thread_updates`
- `daemon/handlers/sync_thread.py`
- `daemon/handlers/update_thread.py`

行为约定：

- 列表/搜索/预览优先高权限账号。
- `sync_thread` 和 `update_thread` 先按默认账号跑，遇到权限提示后再按所需权限重试。
- 如果没有配置账号池，继续走原来的单账号逻辑。

## 5. 错误处理

解析到“阅读权限高于 xx 才能浏览”时，会抛出 `ThreadPermissionRequiredError`，并向上游映射为 `REMOTE_THREAD_PERMISSION_REQUIRED`。这样上层只要知道“需要更高权限”就行，不用自己解析页面文案。

## 6. 不做的事

- 不做多进程共享租约。
- 不做数据库级账号健康管理。
- 不做验证码和二次验证。
- 不做复杂调度器。

这些都不是当前这版要解决的问题。
