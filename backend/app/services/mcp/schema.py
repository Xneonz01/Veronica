"""A tool's JSON Schema as the flat argument model the kernel validates with.

The registry takes flat arguments only (`kernel/registry.py`), and a model told what it
may send is validated against exactly that before anything reaches the server. So a
tool's `inputSchema` is read for what it can be, and refused, with the reason the user
is shown, when it can't:

* a property is a string (an `enum` of strings becomes a `Literal`; `maxLength` kept,
  at most `MAX_STRING`), an integer, a number, a boolean, or a list of one of those, and
  may be null where the schema allows it;
* its name is one a model can be told and a Python field can hold;
* `required` is kept, and anything the schema doesn't name is refused (`extra: forbid`);
* nothing else: no nested objects, no `$ref` (and a reference is never fetched), no
  `anyOf` or `oneOf`, whose checking can be made to cost without bound.

Descriptions are a stranger's text: told to the model with what renders as nothing
taken out (`clean`), shown to the user with it made visible (`shown`), cut in both, and
told at all only once the user has approved the tool (`tools.py`).
"""

from __future__ import annotations

import keyword
import re
from typing import Annotated, Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, create_model

from app.core.text_safety import clean_untrusted, reveal

MAX_STRING = 20_000
MAX_PROPERTIES = 32
MAX_ITEMS = 100
MAX_ENUM = 64
DESCRIPTION = 300
_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}")
_SIMPLE: dict[str, type] = {"string": str, "integer": int, "number": float, "boolean": bool}


class Unsupported(ValueError):
    """A schema that can't be offered as flat arguments; the message is for the user."""


def clean(text: Any, limit: int = DESCRIPTION) -> str:
    """A description as the model is told it: what renders as nothing removed, one line,
    cut where it says so."""
    return _cut(clean_untrusted(text) if isinstance(text, str) else "", limit)


def shown(text: Any, limit: int = DESCRIPTION) -> str:
    """A description as the user reads it to approve it: what renders as nothing shown
    (`⟪U+200B⟫`), so they read what the model would."""
    return _cut(reveal(text) if isinstance(text, str) else "", limit)


def _cut(text: str, limit: int) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"


def arguments_model(tool: str, schema: Any) -> type[BaseModel]:
    """The model `schema` describes, or `Unsupported`."""
    if not isinstance(schema, dict) or schema.get("type", "object") != "object":
        raise Unsupported("its arguments aren't described as an object")
    _no_references(schema)
    properties = schema.get("properties") or {}
    if not isinstance(properties, dict):
        raise Unsupported("its arguments' list can't be read")
    if len(properties) > MAX_PROPERTIES:
        raise Unsupported(f"it takes more than {MAX_PROPERTIES} arguments")
    required = schema.get("required") or []
    if not isinstance(required, list) or any(name not in properties for name in required):
        raise Unsupported("it requires an argument it doesn't describe")
    fields: dict[str, Any] = {}
    for name, spec in properties.items():
        if not isinstance(name, str) or not _usable(name):
            raise Unsupported(f"an argument's name can't be used: {clean(name, 64)!r}")
        kind, limits = _field(name, spec)
        if name in required:
            fields[name] = (kind, Field(**limits))
        else:
            fields[name] = (kind | None, Field(default=None, **limits))
    config = ConfigDict(extra="forbid", protected_namespaces=())
    model = create_model(f"Mcp_{tool}"[:60], __config__=config, **fields)
    return cast("type[BaseModel]", model)


def _usable(name: str) -> bool:
    return (
        _NAME.fullmatch(name) is not None
        and not keyword.iskeyword(name)
        and not hasattr(BaseModel, name)
    )


def _no_references(value: Any, depth: int = 0) -> None:
    """Refuse a schema that refers or composes anywhere, or nests absurdly deep."""
    if depth > 8:
        raise Unsupported("its arguments are nested too deep")
    if isinstance(value, dict):
        for key in ("$ref", "$dynamicRef", "anyOf", "oneOf", "allOf", "not"):
            if key in value:
                raise Unsupported("its arguments refer to other schemas")
        for item in value.values():
            _no_references(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _no_references(item, depth + 1)


def _field(name: str, spec: Any) -> tuple[Any, dict[str, Any]]:
    if not isinstance(spec, dict):
        raise Unsupported(f"{name} isn't described")
    kind, nullable = _type(name, spec.get("type"))
    limits: dict[str, Any] = {}
    description = clean(spec.get("description"))
    if description:
        limits["description"] = description
    if kind == "array":
        items = spec.get("items")
        inner, _ = _type(name, items.get("type") if isinstance(items, dict) else None)
        if inner == "array":
            raise Unsupported(f"{name} is a list of lists")
        python: Any = list[_bounded(inner)]  # type: ignore[misc]
        limits["max_length"] = MAX_ITEMS
    elif kind == "string" and "enum" in spec:
        python = _choices(name, spec["enum"])
    else:
        python = _SIMPLE[kind]
        if kind == "number":  # NaN and infinity aren't JSON, and would be sent as such
            limits["allow_inf_nan"] = False
        if kind == "string":
            limit = spec.get("maxLength")
            bounded = isinstance(limit, int) and 0 < limit < MAX_STRING
            limits["max_length"] = limit if bounded else MAX_STRING
    return (python | None if nullable else python), limits


def _bounded(kind: str) -> Any:
    """A list's item type, held to the same bounds a single value is."""
    if kind == "string":
        return Annotated[str, Field(max_length=MAX_STRING)]
    if kind == "number":
        return Annotated[float, Field(allow_inf_nan=False)]
    return _SIMPLE[kind]


def _type(name: str, declared: Any) -> tuple[str, bool]:
    """The one type a property has, and whether it may also be null."""
    types = declared if isinstance(declared, list) else [declared]
    nullable = "null" in types
    rest = [t for t in types if t != "null"]
    if len(rest) != 1 or rest[0] not in (*_SIMPLE, "array"):
        raise Unsupported(f"{name} isn't a string, number, true-or-false or a list of one")
    return rest[0], nullable


def _choices(name: str, values: Any) -> Any:
    if (
        not isinstance(values, list)
        or not values
        or len(values) > MAX_ENUM
        or not all(isinstance(v, str) and 0 < len(v) <= 200 for v in values)
    ):
        raise Unsupported(f"{name}'s choices can't be used")
    return Literal[tuple(values)]
