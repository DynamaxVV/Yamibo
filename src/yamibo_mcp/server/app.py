from __future__ import annotations

from yamibo_mcp.server.cli import main
from yamibo_mcp.server.mcp_registry import build_mcp_server


__all__ = ["build_mcp_server", "main"]


if __name__ == "__main__":
    main()
