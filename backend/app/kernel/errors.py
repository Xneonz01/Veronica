"""Typed failure codes for the capability kernel.

Every kernel failure carries a machine `code` that the frontend maps to user-facing
copy, so a raw exception message never reaches the user. Codes are stable strings:
renaming one is a UI-visible change.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import ValidationError

_MAX_DETAIL = 160


class Code(StrEnum):
    """Machine-readable reasons a capability request did not succeed."""

    # Pipeline
    unknown_capability = "unknown_capability"
    invalid_args = "invalid_args"
    policy_denied = "policy_denied"
    confirm_denied = "confirm_denied"
    confirm_timeout = "confirm_timeout"
    confirm_unavailable = "confirm_unavailable"  # the prompt couldn't reach anyone
    capability_timeout = "capability_timeout"
    capability_failed = "capability_failed"
    unavailable = "unavailable"  # switched off or withdrawn since it was offered
    budget_exceeded = "budget_exceeded"  # done as often as a turn or a minute allows
    # Paths / files
    path_invalid = "path_invalid"
    path_outside_roots = "path_outside_roots"
    path_protected = "path_protected"
    not_found = "not_found"
    parent_missing = "parent_missing"
    already_exists = "already_exists"
    file_too_large = "file_too_large"
    binary_file = "binary_file"
    # Apps
    app_not_allowed = "app_not_allowed"
    # Memory
    memory_unavailable = "memory_unavailable"
    sensitive_content = "sensitive_content"
    too_many_matches = "too_many_matches"
    # The web (ADR 0003)
    network_disabled = "network_disabled"  # web search is off in Settings
    host_not_allowed = "host_not_allowed"  # a private address, or a host the user blocked
    fetch_failed = "fetch_failed"
    fetch_too_large = "fetch_too_large"
    not_a_page = "not_a_page"
    search_unavailable = "search_unavailable"
    # Tool servers (`services/mcp`).
    server_unavailable = "server_unavailable"  # not running, or stopped mid-call
    tool_error = "tool_error"  # the server's tool said it didn't work
    tool_needs_input = "tool_needs_input"  # it asked for input she can't give
    # Persona
    persona_too_long = "persona_too_long"
    persona_fills_window = "persona_fills_window"
    sensitive_persona = "sensitive_persona"


def safe_validation_detail(exc: ValidationError) -> str:
    """A one-line summary of a validation failure that cannot leak the input.

    `ValidationError.errors()` includes the offending value, which may be a password
    or a file body, so only the field name and the message are used.
    """
    errors = exc.errors()
    if not errors:
        return "Those arguments weren't valid."
    first = errors[0]
    field = ".".join(str(part) for part in first.get("loc", ())) or "argument"
    message = str(first.get("msg", "is not valid"))
    return f"{field}: {message}"[:_MAX_DETAIL]
