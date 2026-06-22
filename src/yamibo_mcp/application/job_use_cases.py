"""已废弃的兼容 re-export。

新代码请改用 `yamibo_mcp.application.job_queries`。
"""

from __future__ import annotations

from yamibo_mcp.application.job_queries import get_job_status_payload

__all__ = ["get_job_status_payload"]
