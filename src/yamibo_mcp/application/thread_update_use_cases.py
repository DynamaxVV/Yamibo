"""已废弃的兼容 re-export。

新代码请改用 `yamibo_mcp.application.update_commands` 与
`yamibo_mcp.application.update_queries`。
"""

from __future__ import annotations

from yamibo_mcp.application.update_commands import create_update_thread_job
from yamibo_mcp.application.update_queries import check_thread_updates

__all__ = ["check_thread_updates", "create_update_thread_job"]
