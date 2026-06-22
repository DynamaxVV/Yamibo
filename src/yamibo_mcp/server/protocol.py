"""已废弃的兼容 re-export。

新代码不要再从本模块导入；请直接使用 `yamibo_mcp.server.legacy_protocol`
或新的 FastMCP 注册入口 `yamibo_mcp.server.mcp_registry`。
"""

from __future__ import annotations

from yamibo_mcp.server.legacy_protocol import *  # noqa: F403
