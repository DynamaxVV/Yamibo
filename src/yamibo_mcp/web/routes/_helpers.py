from __future__ import annotations

import json
from decimal import Decimal
from datetime import date, datetime
from http import HTTPStatus


def json_default(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        integral = value.to_integral_value()
        return int(value) if value == integral else float(value)
    raise TypeError(f"Object of type {value.__class__.__name__} is not JSON serializable")


def jsonish_loads(value, default):
    if value is None or value == "":
        return default
    if isinstance(value, (dict, list)):
        return value
    return json.loads(value)


def json_response(handler, data, status=HTTPStatus.OK):
    body = json.dumps(data, ensure_ascii=False, default=json_default).encode("utf-8")
    try:
        handler.send_response(status.value)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)
    except (BrokenPipeError, ConnectionResetError):
        return


def read_json_body(handler) -> dict:
    length = int(handler.headers.get("Content-Length") or "0")
    raw = handler.rfile.read(length).decode("utf-8") if length else "{}"
    return json.loads(raw)


def error_response(handler, message, status=HTTPStatus.BAD_REQUEST):
    json_response(handler, {"error": message}, status)
