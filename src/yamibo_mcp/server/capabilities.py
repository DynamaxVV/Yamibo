"""Machine-readable capability contracts derived from public MCP tools."""

from __future__ import annotations

import inspect
import types
from collections.abc import Iterable
from typing import Any, Union, get_args, get_origin

EFFECTS = frozenset({"read_only", "remote_read", "enqueue_job", "mutating", "destructive"})
RISK_LEVELS = frozenset({"read", "remote_read", "write", "sensitive_write", "destructive"})
TIMEOUT_CLASSES = frozenset({"short", "polling", "background"})
IDEMPOTENCY_MODES = frozenset({
    "safe_to_retry",
    "deduplicated_by_active_job",
    "not_deduplicated",
})
_REQUIRED_METADATA = frozenset({
    "version",
    "effect",
    "risk",
    "idempotency",
    "requires",
    "cost_hints",
    "produces",
    "followups",
    "timeout_class",
})


def build_capability_manifest(
    public_tools: Iterable[tuple[str, str, Any]],
) -> dict[str, Any]:
    """Build the versioned manifest from the MCP registration source."""
    registrations = list(public_tools)
    names = [name for name, _, _ in registrations]
    if len(names) != len(set(names)):
        raise ValueError("PUBLIC_AGENT_TOOLS contains duplicate names")

    capabilities = [
        _build_capability(name, description, handler)
        for name, description, handler in registrations
    ]
    _validate_followups(capabilities)

    return {
        "schema_version": "1",
        "effects": sorted(EFFECTS),
        "risk_levels": sorted(RISK_LEVELS),
        "capabilities": capabilities,
    }


def _build_capability(name: str, description: str, handler: Any) -> dict[str, Any]:
    metadata = dict(_metadata_for(name, handler))
    input_any_of = metadata.pop("input_any_of", None)
    capability = {
        "name": name,
        "description": description,
        "input_schema": _input_schema(handler, input_any_of=input_any_of),
        "output_schema": _agent_result_schema(),
        **metadata,
    }
    return capability


def _metadata_for(name: str, handler: Any) -> dict[str, Any]:
    metadata = getattr(handler, "__capability_metadata__", None)
    if not isinstance(metadata, dict):
        raise ValueError(f"public capability {name!r} has no registered metadata")
    missing = _REQUIRED_METADATA - metadata.keys()
    if missing:
        raise ValueError(f"public capability {name!r} is missing metadata: {sorted(missing)}")
    unknown = metadata.keys() - (_REQUIRED_METADATA | {"input_any_of", "terminal_read"})
    if unknown:
        raise ValueError(f"public capability {name!r} has unknown metadata: {sorted(unknown)}")
    if metadata["effect"] not in EFFECTS:
        raise ValueError(f"public capability {name!r} has invalid effect")
    if metadata["risk"] not in RISK_LEVELS:
        raise ValueError(f"public capability {name!r} has invalid risk")
    if metadata["timeout_class"] not in TIMEOUT_CLASSES:
        raise ValueError(f"public capability {name!r} has invalid timeout class")
    expected_risks = {
        "read_only": {"read"},
        "remote_read": {"remote_read"},
        "enqueue_job": {"write", "sensitive_write"},
        "mutating": {"write", "sensitive_write"},
        "destructive": {"destructive"},
    }
    if metadata["risk"] not in expected_risks[metadata["effect"]]:
        raise ValueError(f"public capability {name!r} has inconsistent effect and risk")
    if metadata["effect"] == "destructive":
        raise ValueError(f"destructive capability {name!r} cannot be public")
    idempotency = metadata["idempotency"]
    if not isinstance(idempotency, dict) or idempotency.get("mode") not in IDEMPOTENCY_MODES:
        raise ValueError(f"public capability {name!r} has invalid idempotency")
    if metadata["effect"] == "enqueue_job" and "terminal_read" not in metadata:
        raise ValueError(f"queued capability {name!r} must declare terminal_read")
    if metadata["effect"] != "enqueue_job" and "terminal_read" in metadata:
        raise ValueError(f"non-queued capability {name!r} cannot declare terminal_read")
    terminal_read = metadata.get("terminal_read")
    terminal_fields = {"job_id_paths", "tool", "resource", "completion_field"}
    if terminal_read is not None and (
        not isinstance(terminal_read, dict)
        or not terminal_fields.issubset(terminal_read)
    ):
        raise ValueError(f"queued capability {name!r} has invalid terminal_read")
    return metadata


def _validate_followups(capabilities: list[dict[str, Any]]) -> None:
    names = {item["name"] for item in capabilities}
    for item in capabilities:
        unknown = set(item["followups"]) - names
        if unknown:
            raise ValueError(f"capability {item['name']!r} has unknown followups: {sorted(unknown)}")


def _input_schema(
    handler: Any,
    *,
    input_any_of: list[list[str]] | None = None,
) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, parameter in inspect.signature(handler, eval_str=True).parameters.items():
        properties[name] = _parameter_schema(parameter)
        if parameter.default is inspect.Parameter.empty:
            required.append(name)
    schema: dict[str, Any] = {"type": "object", "properties": properties, "additionalProperties": False}
    if required:
        schema["required"] = required
    if input_any_of:
        schema["anyOf"] = [{"required": fields} for fields in input_any_of]
    return schema


def _parameter_schema(parameter: inspect.Parameter) -> dict[str, Any]:
    annotation = parameter.annotation
    origin = get_origin(annotation)
    args = get_args(annotation)
    nullable = origin in {Union, types.UnionType} and type(None) in args
    non_none_args = [arg for arg in args if arg is not type(None)]
    target = non_none_args[0] if nullable and non_none_args else annotation
    target_origin = get_origin(target)
    if target is int:
        value_type = "integer"
    elif target is float:
        value_type = "number"
    elif target is bool:
        value_type = "boolean"
    elif target_origin is list or target is list:
        value_type = "array"
    elif target_origin is dict or target is dict:
        value_type = "object"
    else:
        value_type = "string"
    schema: dict[str, Any] = {"type": value_type}
    list_args = get_args(target)
    if target_origin is list and list_args:
        schema["items"] = {"type": _json_type(list_args[0])}
    if nullable:
        schema["type"] = [value_type, "null"]
    if parameter.default is not inspect.Parameter.empty:
        schema["default"] = parameter.default
    return schema


def _json_type(annotation: Any) -> str:
    types_by_annotation = {int: "integer", float: "number", bool: "boolean"}
    return types_by_annotation.get(annotation, "string")


def _agent_result_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "required": [
            "ok",
            "data",
            "error",
            "resources",
            "next_actions",
            "warnings",
            "side_effects",
        ],
        "properties": {
            "ok": {"type": "boolean"},
            "data": {},
            "error": {},
            "resources": {"type": "object"},
            "next_actions": {"type": "array"},
            "warnings": {"type": "array"},
            "side_effects": {"type": "array"},
        },
    }
