from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from yamibo_mcp.config import load_settings
from yamibo_mcp.logging import configure_logging
from yamibo_mcp.web_fastapi.app import EmbeddedWebServer
from yamibo_mcp.daemon.runner import DaemonRunner


def _check_duplicate_daemon(data_dir) -> str | None:
    """检查是否已有 daemon 在运行。

    如果已有进程，打印警告但**不阻断** —— 多 daemon 并发是允许的，
    lease 机制保证不会重复领取同一任务。
    返回 PID 文件路径，调用方应在关闭时删除。
    """
    pid_file = data_dir / "daemon.pid"
    if pid_file.exists():
        try:
            old_pid = int(pid_file.read_text().strip())
            os.kill(old_pid, 0)  # 信号 0 仅检查进程是否存在
            print(
                f"⚠ 已有 daemon 在运行 (PID {old_pid})。"
                f"多 daemon 并发通过 lease 机制协调，"
                f"但如果多个 daemon 同时绑定同一端口会导致端口冲突。",
                file=sys.stderr,
            )
        except (OSError, ValueError):
            pid_file.unlink(missing_ok=True)
    pid_file.write_text(str(os.getpid()))
    return str(pid_file)


def _release_pid_file(pid_file: str | None) -> None:
    if pid_file is not None:
        try:
            Path(pid_file).unlink(missing_ok=True)
        except Exception:
            pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Run YamiboArchiver daemon.")
    parser.add_argument("--once", action="store_true", help="Process at most one job and exit.")
    parser.add_argument("--worker-id", help="Override worker id.")
    parser.add_argument("--no-web", action="store_true", help="Do not start the embedded web console.")
    args = parser.parse_args()

    configure_logging()
    settings = load_settings()
    pid_file: str | None = None
    if not args.once:
        pid_file = _check_duplicate_daemon(settings.data_dir)
    runner = DaemonRunner(settings, worker_id=args.worker_id)
    embedded_web: EmbeddedWebServer | None = None
    try:
        if not args.no_web:
            embedded_web = EmbeddedWebServer(settings)
            embedded_web.start()
        if args.once:
            result = runner.run_once()
            print(f"processed={result.processed}")
        else:
            runner.run_forever()
    except KeyboardInterrupt:
        print("daemon interrupted")
    finally:
        _release_pid_file(pid_file)
        if embedded_web is not None:
            embedded_web.stop()


if __name__ == "__main__":
    main()
