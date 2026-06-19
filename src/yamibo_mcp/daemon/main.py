from __future__ import annotations

import argparse

from yamibo_mcp.config import load_settings
from yamibo_mcp.logging import configure_logging
from yamibo_mcp.web.app import EmbeddedWebServer
from yamibo_mcp.daemon.runner import DaemonRunner


def main() -> None:
    parser = argparse.ArgumentParser(description="Run YamiboMCP daemon.")
    parser.add_argument("--once", action="store_true", help="Process at most one job and exit.")
    parser.add_argument("--worker-id", help="Override worker id.")
    parser.add_argument("--no-web", action="store_true", help="Do not start the embedded web console.")
    args = parser.parse_args()

    configure_logging()
    settings = load_settings()
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
        if embedded_web is not None:
            embedded_web.stop()


if __name__ == "__main__":
    main()
