from __future__ import annotations

from fastapi import Depends, Request

from yamibo_mcp.config import Settings
from yamibo_mcp.db.connection import connect
from yamibo_mcp.db.migrations import migrate
from yamibo_mcp.services.web_chat import ChatService


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_chat_service(request: Request) -> ChatService:
    return request.app.state.chat_service


def get_conn(settings: Settings = Depends(get_settings)):
    conn = connect(settings.db_path)
    try:
        migrate(conn)
        yield conn
    finally:
        conn.close()
