"""已废弃的兼容 re-export。

新代码请改用 `yamibo_mcp.application.archive_commands` 与
`yamibo_mcp.application.legacy_use_cases`。
"""

from __future__ import annotations

from yamibo_mcp.application.archive_commands import archive_thread_job
from yamibo_mcp.application.legacy_use_cases import ensure_thread

__all__ = ["archive_thread_job", "ensure_thread"]
