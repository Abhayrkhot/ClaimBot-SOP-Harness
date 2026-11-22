"""Provider-neutral request and response types for LLM calls."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class LLMRequest:
    stage: str
    system: str
    user: str
    schema: dict[str, Any] | None = None
    """JSON schema for constrained output; None for free text."""
    schema_name: str = "Output"
    max_tokens: int = 1024
    temperature: float = 0.0


@dataclass(frozen=True)
class LLMResponse:
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0


class ProviderError(RuntimeError):
    """A provider call failed. ``retryable`` errors count against the circuit breaker."""

    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


class LLMUnavailable(RuntimeError):
    """No provider produced a response within the turn budget."""


class LLMOutputError(ValueError):
    """The response was not valid JSON (or was truncated)."""


class Provider(Protocol):
    name: str
    model: str

    async def complete(self, request: LLMRequest, timeout: float) -> LLMResponse: ...


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.S)


def parse_json(text: str) -> dict[str, Any]:
    """Parse a JSON object, tolerating code fences and a leading reasoning preamble."""
    cleaned = _FENCE.sub("", text.strip())
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start < 0 or end <= start:
            raise LLMOutputError("response is not JSON") from None
        try:
            value = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError as exc:
            raise LLMOutputError("response is not JSON") from exc
    if not isinstance(value, dict):
        raise LLMOutputError("response JSON is not an object")
    return value


def portable_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Reduce a Pydantic JSON schema to the subset every structured-output backend accepts.

    Inlines ``$defs``, closes every object (all properties required, no extras) and drops
    validation keywords (patterns, lengths, bounds, defaults) that some backends reject. The
    dropped constraints are still enforced locally by Pydantic after the call.
    """
    defs = schema.get("$defs", {})
    keep = {"type", "properties", "required", "items", "enum", "anyOf", "description", "additionalProperties"}

    def walk(node: Any) -> Any:
        if isinstance(node, list):
            return [walk(n) for n in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            target = walk(defs[node["$ref"].split("/")[-1]])
            if "description" in node:
                target = {**target, "description": node["description"]}
            return target
        if "const" in node:
            node = {**node, "enum": [node["const"]]}
        out = {k: walk(v) for k, v in node.items() if k in keep}
        if out.get("type") == "object" or "properties" in out:
            out["type"] = "object"
            out["properties"] = {k: walk(v) for k, v in node.get("properties", {}).items()}
            out["required"] = list(out["properties"])
            out["additionalProperties"] = False
        return out

    return walk({k: v for k, v in schema.items() if k != "$defs"})  # type: ignore[no-any-return]
