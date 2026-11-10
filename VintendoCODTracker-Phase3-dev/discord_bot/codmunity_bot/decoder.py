"""Defensive decoder for CODMunity's typed server-function response."""
from __future__ import annotations

from typing import Any


class DecodeError(ValueError):
    pass


def decode_typed(value: Any, *, _depth: int = 0) -> Any:
    """Decode the observed t:10 object / t:9 array envelope.

    Unknown types remain explicit dictionaries. Null stays None and absent keys
    stay absent; malformed envelopes never silently become empty stats.
    """
    if _depth > 100:
        raise DecodeError("typed response exceeded nesting limit")
    if isinstance(value, list):
        return [decode_typed(item, _depth=_depth + 1) for item in value]
    if not isinstance(value, dict) or "t" not in value:
        return value
    kind = value.get("t")
    if kind == 0:
        return None
    if kind == 1:
        return value.get("s")
    if kind in (2, 3, 4, 5, 6, 7, 8):
        # Keep scalar interpretation conservative: preserve the payload exactly.
        for key in ("v", "n", "b", "s"):
            if key in value:
                return value[key]
        return {key: decode_typed(item, _depth=_depth + 1) for key, item in value.items() if key != "t"}
    if kind == 9:
        payload = value.get("p", value.get("v", value.get("a", [])))
        if payload is None:
            return None
        if isinstance(payload, dict):
            payload = payload.get("a", payload.get("v", payload))
        if isinstance(payload, list):
            return [decode_typed(item, _depth=_depth + 1) for item in payload]
        return {"$type": 9, "value": decode_typed(payload, _depth=_depth + 1)}
    if kind == 10:
        payload = value.get("p")
        if not isinstance(payload, dict):
            return {"$type": 10, "value": decode_typed(payload, _depth=_depth + 1)}
        keys, vals = payload.get("k"), payload.get("v")
        if not isinstance(keys, list) or not isinstance(vals, list):
            return {"$type": 10, "value": decode_typed(payload, _depth=_depth + 1)}
        result = {}
        for index, key in enumerate(keys):
            if not isinstance(key, str):
                continue
            if index < len(vals):
                result[key] = decode_typed(vals[index], _depth=_depth + 1)
        return result
    return {key: decode_typed(item, _depth=_depth + 1) for key, item in value.items()}


def extract_result(envelope: Any) -> dict[str, Any]:
    """Decode an envelope and return its object, rejecting invalid roots."""
    decoded = decode_typed(envelope)
    if not isinstance(decoded, dict):
        raise DecodeError("CODMunity response root was not an object")
    return decoded
