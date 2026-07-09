from __future__ import annotations

import argparse
import inspect

from yamibo_mcp.logging import configure_logging
from yamibo_mcp.server.mcp_registry import build_mcp_server


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Yamibo MCP server.")
    sub = parser.add_subparsers(dest="command")
    stdio_parser = sub.add_parser("stdio")
    stdio_parser.add_argument("--transport", choices=["stdio", "sse", "streamable-http"], default="stdio")
    stdio_parser.add_argument("--host", default="0.0.0.0")
    stdio_parser.add_argument("--port", type=int, default=8000)
    stdio_parser.add_argument("--path", default="/sse")
    return parser


def _run_server(transport: str, host: str, port: int, path: str) -> None:
    server = build_mcp_server(host=host, port=port, sse_path=path, mount_path="/")
    run = server.run
    params = inspect.signature(run).parameters
    kwargs: dict[str, object] = {"transport": transport}
    if "mount_path" in params:
        kwargs["mount_path"] = None if path == "/sse" else path
    run(**kwargs)


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    configure_logging()

    command = args.command or "stdio"
    if command == "stdio":
        _run_server(transport=args.transport, host=args.host, port=args.port, path=args.path)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
