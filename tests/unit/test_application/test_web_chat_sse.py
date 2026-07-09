from __future__ import annotations

from types import SimpleNamespace

from yamibo_mcp.services.web_chat import _extract_openai_delta, _parse_sse_event, iter_sse_events


class _FakeSseResponse:
    def __init__(self, chunks: list[bytes]):
        self._chunks = chunks

    def read(self, size: int = -1) -> bytes:
        if not self._chunks:
            return b""
        return self._chunks.pop(0)


def test_parse_sse_event_handles_openai_delta_content():
    event = _parse_sse_event(
        'data: {"id":"chatcmpl-1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"Hel"}}]}'
    )

    assert event == {
        'type': 'delta',
        'content': 'Hel',
        'raw_output': {
            'id': 'chatcmpl-1',
            'object': 'chat.completion.chunk',
            'choices': [
                {
                    'index': 0,
                    'delta': {'content': 'Hel'},
                }
            ],
        },
    }


def test_parse_sse_event_handles_done_marker():
    assert _parse_sse_event('data: [DONE]') == {'type': 'done'}


def test_parse_sse_event_merges_multiline_data_blocks():
    event = _parse_sse_event(
        'data: {"choices":[{"delta":{"content":"Hello"}}]}\n' \
        'data: {"choices":[{"delta":{"content":" world"}}]}'
    )

    assert event == {
        'type': 'meta',
        'raw_output': {
            'choices': [
                {
                    'delta': {'content': ' world'},
                }
            ],
        },
    }


def test_extract_openai_delta_prefers_delta_content_and_supports_array_content():
    assert _extract_openai_delta({'choices': [{'delta': {'content': 'hello'}}]}) == 'hello'
    assert _extract_openai_delta({'choices': [{'delta': {'content': [{'text': 'he'}, {'text': 'llo'}]}}]}) == 'hello'
    assert _extract_openai_delta({'choices': [{'message': {'content': 'fallback'}}]}) == 'fallback'


def test_iter_sse_events_yields_delta_and_done_events():
    response = _FakeSseResponse(
        [
            b'data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n',
            b'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n',
            b'data: [DONE]\n\n',
        ]
    )

    events = list(iter_sse_events(response))

    assert events == [
        {'type': 'delta', 'content': 'Hel', 'raw_output': {'choices': [{'delta': {'content': 'Hel'}}]}},
        {'type': 'delta', 'content': 'lo', 'raw_output': {'choices': [{'delta': {'content': 'lo'}}]}},
        {'type': 'done'},
    ]
