from __future__ import annotations

import http.client
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlparse, urljoin
from urllib.request import Request, urlopen

from yamibo_mcp.config import Settings
from yamibo_mcp.storage.atomic import atomic_write_text

_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.S)
_CHAT_RUNTIME_DIR_NAME = 'chat'
_CHAT_SESSIONS_FILE_NAME = 'sessions.json'
_CHAT_COMMAND_TIMEOUT_SECONDS = 180
_MAX_HISTORY_ITEMS = 8


@dataclass(frozen=True)
class HermesHttpConfig:
    endpoint: str
    api_key: str | None
    model: str
    stream: bool


def get_chat_context(settings: Settings) -> dict[str, Any]:
    sessions = _load_sessions(settings.project_root)
    status = _probe_hermes_status(settings)
    return {
        'model': settings.hermes_model,
        'transport': 'hermes_http',
        'status': status,
        'transports': [
            {
                'id': 'hermes_http',
                'label': 'Hermes HTTP',
                'description': 'Use Hermes chat/completions over HTTP with the configured endpoint and API key.',
            },
        ],
        'daemon_connected': status['connected'],
        'runtime_files': {'sessions': _session_file(settings.project_root).as_posix()},
        'sessions': sessions,
        'hermes': {'api_key': settings.hermes_api_key, 'model': settings.hermes_model, 'host': settings.hermes_host, 'port': settings.hermes_port, 'endpoint': status['endpoint'], 'stream': getattr(settings, 'hermes_stream', False)},
    }


def run_chat_turn(settings: Settings, *, session_id: str | None, message: str, history: list[dict[str, str]] | None = None) -> dict[str, Any]:
    return _run_chat_turn_common(settings, session_id=session_id, message=message, history=history, stream=False)


def run_chat_turn_stream(settings: Settings, *, session_id: str | None, message: str, history: list[dict[str, str]] | None = None) -> dict[str, Any]:
    return _run_chat_turn_common(settings, session_id=session_id, message=message, history=history, stream=True)


def iter_chat_turn_stream(settings: Settings, *, session_id: str | None, message: str, history: list[dict[str, str]] | None = None):
    prompt = message.strip()
    if not prompt:
        raise ValueError('message required')
    sessions = _load_sessions(settings.project_root)
    session = _ensure_session(sessions, session_id=session_id, prompt=prompt)
    history_items = _normalize_history(history or session.get('messages', []))
    payload = _build_chat_completion_payload(settings, session_id=session['id'], prompt=prompt, history=history_items, stream=True)
    events: list[dict[str, Any]] = []
    assistant_parts: list[str] = []
    status = 200
    for event in _iter_hermes_http_stream(settings, payload=payload):
        status = int(event.pop('_status', status))
        events.append(event)
        if event.get('type') == 'delta' and event.get('content'):
            assistant_parts.append(str(event['content']))
        yield event
        if event.get('type') == 'done':
            break
    assistant_message = ''.join(assistant_parts)
    turn = {'role': 'assistant', 'content': assistant_message, 'raw_output': {'events': events}, 'created_at': _now_iso()}
    session['messages'].append({'role': 'user', 'content': prompt, 'created_at': _now_iso()})
    session['messages'].append(turn)
    session['updated_at'] = _now_iso()
    if len(session['messages']) > 40:
        session['messages'] = session['messages'][-40:]
    _upsert_session(sessions, session)
    _save_sessions(settings.project_root, sessions)
    yield {'type': 'done', 'status': status}


def _run_chat_turn_common(settings: Settings, *, session_id: str | None, message: str, history: list[dict[str, str]] | None, stream: bool) -> dict[str, Any]:
    prompt = message.strip()
    if not prompt:
        raise ValueError('message required')
    sessions = _load_sessions(settings.project_root)
    session = _ensure_session(sessions, session_id=session_id, prompt=prompt)
    history_items = _normalize_history(history or session.get('messages', []))
    payload = _build_chat_completion_payload(settings, session_id=session['id'], prompt=prompt, history=history_items, stream=stream)
    if stream:
        response = _run_hermes_http_stream(settings, payload=payload)
        completed = {'status': response['status']}
        raw_output = response['raw_output']
        assistant_message = response['assistant_message']
        stream_events = response['events']
    else:
        status, stdout, stderr = _run_hermes_http(settings, payload=payload)
        completed = {'status': status}
        raw_output = _parse_jsonish(stdout)
        assistant_message = _extract_assistant_message(raw_output) or '已处理你的请求。'
        stream_events = []
    turn = {'role': 'assistant', 'content': assistant_message, 'raw_output': raw_output, 'created_at': _now_iso()}
    session['messages'].append({'role': 'user', 'content': prompt, 'created_at': _now_iso()})
    session['messages'].append(turn)
    session['updated_at'] = _now_iso()
    if len(session['messages']) > 40:
        session['messages'] = session['messages'][-40:]
    _upsert_session(sessions, session)
    _save_sessions(settings.project_root, sessions)
    return {
        'assistant_message': assistant_message,
        'model': settings.hermes_model,
        'transport': 'hermes_http',
        'stream': stream,
        'stream_events': stream_events,
        'commands': [{'command': 'POST /v1/chat/completions', 'args': payload, 'reason': 'Use Hermes chat/completions to process the conversation session.', 'transport': 'hermes_http', 'executed': True, 'ok': completed.get('status', 200) < 400, 'invocation': f"POST {payload['endpoint']}", 'stdout': '', 'stderr': '', 'output': raw_output, 'returncode': completed.get('status', 200), 'warning': None, 'request': payload}],
        'warnings': [],
        'runtime_files': {'sessions': _session_file(settings.project_root).as_posix()},
        'session': session,
    }


def list_chat_sessions(project_root: Path) -> list[dict[str, Any]]:
    return _load_sessions(project_root)


def create_chat_session(project_root: Path, *, title: str | None = None) -> dict[str, Any]:
    sessions = _load_sessions(project_root)
    session = {
        'id': f'chat-{int(datetime.now(tz=timezone.utc).timestamp() * 1000)}',
        'title': title or '新对话',
        'created_at': _now_iso(),
        'updated_at': _now_iso(),
        'messages': [],
    }
    sessions.append(session)
    _save_sessions(project_root, sessions)
    return session


def delete_chat_session(project_root: Path, session_id: str) -> dict[str, Any]:
    sessions = _load_sessions(project_root)
    next_sessions = [item for item in sessions if item.get('id') != session_id]
    _save_sessions(project_root, next_sessions)
    return {'ok': True, 'deleted': len(sessions) - len(next_sessions)}


def probe_hermes_chat_completions(settings: Settings) -> dict[str, Any]:
    response = _openai_chat_completion(settings, messages=[{'role': 'user', 'content': 'Hello!'}], stream=False)
    ok = response['status'] < 400
    label = 'Hermes 已连接' if ok else f'Hermes 响应异常: HTTP {response["status"]}'
    return {
        'connected': ok,
        'endpoint': response['endpoint'],
        'label': label,
        'status': response['status'],
        'stdout': response.get('text', '')[:2000],
        'stderr': response.get('stderr', '')[:2000],
        'request': response.get('request'),
    }


def _probe_hermes_status(settings: Settings) -> dict[str, Any]:
    try:
        result = probe_hermes_chat_completions(settings)
        return {
            'connected': result['connected'],
            'kind': 'host.docker.internal' if settings.hermes_host == 'host.docker.internal' else 'custom_host',
            'endpoint': result['endpoint'],
            'label': result['label'],
        }
    except Exception as exc:
        return {'connected': False, 'kind': 'host.docker.internal' if settings.hermes_host == 'host.docker.internal' else 'custom_host', 'endpoint': f'http://{settings.hermes_host}:{settings.hermes_port}', 'label': f'Hermes 未连接: {exc.__class__.__name__}'}


def _build_chat_completion_payload(settings: Settings, *, session_id: str, prompt: str, history: list[dict[str, str]], stream: bool) -> dict[str, Any]:
    messages = [{'role': item['role'], 'content': item['content']} for item in history]
    messages.append({'role': 'user', 'content': prompt})
    return {
        'endpoint': f'http://{settings.hermes_host}:{settings.hermes_port}/v1/chat/completions',
        'body': {
            'model': settings.hermes_model,
            'messages': messages,
            'stream': stream,
        },
        'session_id': session_id,
    }


def _openai_chat_completion(settings: Settings, *, messages: list[dict[str, str]], stream: bool) -> dict[str, Any]:
    payload = {
        'endpoint': f'http://{settings.hermes_host}:{settings.hermes_port}/v1/chat/completions',
        'body': {'model': settings.hermes_model, 'messages': messages, 'stream': stream},
    }
    if stream:
        response = _run_hermes_http_stream(settings, payload=payload)
        return {
            'status': response['status'],
            'endpoint': payload['endpoint'],
            'text': response['assistant_message'],
            'events': response['events'],
            'stderr': '',
            'request': payload,
        }
    status, text, stderr = _run_hermes_http(settings, payload=payload)
    return {'status': status, 'endpoint': payload['endpoint'], 'text': text, 'stderr': stderr, 'request': payload}


def _run_hermes_http(settings: Settings, *, payload: dict[str, Any]) -> tuple[int, str, str]:
    parsed = urlparse(payload['endpoint'])
    conn_cls = http.client.HTTPSConnection if parsed.scheme == 'https' else http.client.HTTPConnection
    conn = conn_cls(parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80), timeout=_CHAT_COMMAND_TIMEOUT_SECONDS)
    try:
        path = parsed.path or '/v1/chat/completions'
        if parsed.query:
            path = f'{path}?{parsed.query}'
        headers = {'Content-Type': 'application/json'}
        api_key = settings.hermes_api_key
        if api_key:
            headers['Authorization'] = f'Bearer {api_key}'
        body = json.dumps(payload['body'], ensure_ascii=False).encode('utf-8')
        headers['Content-Type'] = 'application/json; charset=utf-8'
        conn.request('POST', path, body=body, headers=headers)
        response = conn.getresponse()
        stdout = response.read().decode('utf-8', errors='replace')
        return response.status, stdout, ''
    finally:
        conn.close()


def _run_hermes_http_stream(settings: Settings, *, payload: dict[str, Any]) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    assistant_parts: list[str] = []
    status = 200
    for event in _iter_hermes_http_stream(settings, payload=payload):
        status = int(event.pop('_status', status))
        events.append(event)
        if event['type'] == 'delta' and event.get('content'):
            assistant_parts.append(str(event['content']))
        if event['type'] == 'done':
            break
    return {'status': status, 'raw_output': {'events': events}, 'assistant_message': ''.join(assistant_parts), 'events': events}


def _iter_hermes_http_stream(settings: Settings, *, payload: dict[str, Any]):
    parsed = urlparse(payload['endpoint'])
    conn_cls = http.client.HTTPSConnection if parsed.scheme == 'https' else http.client.HTTPConnection
    conn = conn_cls(parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80), timeout=_CHAT_COMMAND_TIMEOUT_SECONDS)
    try:
        path = parsed.path or '/v1/chat/completions'
        if parsed.query:
            path = f'{path}?{parsed.query}'
        headers = {'Content-Type': 'application/json', 'Accept': 'text/event-stream'}
        api_key = settings.hermes_api_key
        if api_key:
            headers['Authorization'] = f'Bearer {api_key}'
        body = json.dumps(payload['body'], ensure_ascii=False).encode('utf-8')
        headers['Content-Type'] = 'application/json; charset=utf-8'
        conn.request('POST', path, body=body, headers=headers)
        response = conn.getresponse()
        status = response.status
        for event in iter_sse_events(response):
            event['_status'] = status
            yield event
            if event['type'] == 'done':
                break
    finally:
        conn.close()


def _session_file(project_root: Path) -> Path:
    return Path(project_root) / 'data' / _CHAT_RUNTIME_DIR_NAME / _CHAT_SESSIONS_FILE_NAME


def _load_sessions(project_root: Path) -> list[dict[str, Any]]:
    path = _session_file(project_root)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def _save_sessions(project_root: Path, sessions: list[dict[str, Any]]) -> None:
    path = _session_file(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(sessions, ensure_ascii=False, indent=2))


def _ensure_session(sessions: list[dict[str, Any]], *, session_id: str | None, prompt: str) -> dict[str, Any]:
    if session_id:
        for session in sessions:
            if session.get('id') == session_id:
                return session
    title = prompt.splitlines()[0].strip()[:32] or '新对话'
    session = {'id': f'chat-{int(datetime.now(tz=timezone.utc).timestamp() * 1000)}', 'title': title, 'created_at': _now_iso(), 'updated_at': _now_iso(), 'messages': []}
    sessions.append(session)
    return session


def _upsert_session(sessions: list[dict[str, Any]], session: dict[str, Any]) -> None:
    for idx, item in enumerate(sessions):
        if item.get('id') == session.get('id'):
            sessions[idx] = session
            return
    sessions.append(session)


def _normalize_history(history: list[dict[str, str]]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for item in history[-_MAX_HISTORY_ITEMS:]:
        role = str(item.get('role') or '').strip()
        content = str(item.get('content') or '').strip()
        if role in {'user', 'assistant'} and content:
            items.append({'role': role, 'content': content[:2000]})
    return items


def iter_sse_events(response):
    buffer = ''
    while True:
        chunk = response.read(1024)
        if not chunk:
            break
        buffer += chunk.decode('utf-8', errors='replace')
        while '\n\n' in buffer:
            raw_event, buffer = buffer.split('\n\n', 1)
            event = _parse_sse_event(raw_event)
            if event is not None:
                yield event


def _parse_sse_event(raw_event: str) -> dict[str, Any] | None:
    data_lines: list[str] = []
    for line in raw_event.splitlines():
        if line.startswith('data:'):
            data_lines.append(line[5:].lstrip())
    if not data_lines:
        return None
    data = '\n'.join(data_lines).strip()
    if not data:
        return None
    if data == '[DONE]':
        return {'type': 'done'}
    try:
        payload_obj = json.loads(data)
    except json.JSONDecodeError:
        return {'type': 'meta', 'raw': data}
    delta = _extract_openai_delta(payload_obj)
    if delta:
        return {'type': 'delta', 'content': delta, 'raw_output': payload_obj}
    return {'type': 'meta', 'raw_output': payload_obj}


def _extract_openai_delta(payload_obj: dict[str, Any]) -> str | None:
    choices = payload_obj.get('choices') if isinstance(payload_obj, dict) else None
    if not isinstance(choices, list):
        return None
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        delta = choice.get('delta')
        if isinstance(delta, dict):
            content = delta.get('content')
            if isinstance(content, str) and content:
                return content
            if isinstance(content, list):
                parts: list[str] = []
                for item in content:
                    if isinstance(item, dict):
                        text = item.get('text') or item.get('content')
                        if isinstance(text, str) and text:
                            parts.append(text)
                if parts:
                    return ''.join(parts)
        message = choice.get('message')
        if isinstance(message, dict):
            content = message.get('content')
            if isinstance(content, str) and content:
                return content
    return None


def _parse_jsonish(value: str) -> Any:
    text = value.strip()
    if not text:
        return None
    match = _JSON_BLOCK_RE.search(text)
    if match:
        text = match.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _extract_assistant_message(output: Any) -> str | None:
    if isinstance(output, dict):
        for key in ('assistant_message', 'message', 'content', 'reply'):
            value = output.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        choices = output.get('choices')
        if isinstance(choices, list):
            for choice in choices:
                if not isinstance(choice, dict):
                    continue
                message = choice.get('message')
                if isinstance(message, dict):
                    content = message.get('content')
                    if isinstance(content, str) and content.strip():
                        return content.strip()
                delta = choice.get('delta')
                if isinstance(delta, dict):
                    content = delta.get('content')
                    if isinstance(content, str) and content.strip():
                        return content.strip()
    if isinstance(output, str) and output.strip():
        return output.strip()
    return None


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


__all__ = ['get_chat_context', 'run_chat_turn', 'run_chat_turn_stream', 'iter_chat_turn_stream', 'list_chat_sessions', 'create_chat_session', 'delete_chat_session']
