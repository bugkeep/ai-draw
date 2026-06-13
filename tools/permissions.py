from typing import Callable


class PermissionChecker:
    """Tool-call permission gate.

    Supports two modes:
      - **blocklist**: any tool not in ``_blocked`` is allowed
      - **allowlist**: only tools in ``_allowed`` are allowed (``_allowed``
        must be set via ``allow_only``)

    An optional ``callback`` can inspect the full tool name + arguments and
    return ``True`` (allow) or ``False`` (deny) for fine-grained control.
    """

    def __init__(self, callback: Callable[[str, dict], bool] | None = None):
        self._blocked: set[str] = set()
        self._allowed: set[str] | None = None  # None = all-allowed mode
        self._callback = callback

    # ── configuration ──────────────────────────────────────────────────

    def block(self, *tool_names: str):
        """Add tools to the blocklist."""
        self._blocked.update(tool_names)

    def unblock(self, *tool_names: str):
        """Remove tools from the blocklist."""
        self._blocked.difference_update(tool_names)

    def allow_only(self, *tool_names: str):
        """Switch to allowlist mode — only these tools are permitted.

        Pass no arguments to reset to all-allowed mode.
        """
        self._allowed = set(tool_names) if tool_names else None

    # ── approval ───────────────────────────────────────────────────────

    def approve(self, tool_name: str, args: dict) -> bool:
        """Return ``True`` if the tool call is allowed, ``False`` otherwise."""
        if self._allowed is not None and tool_name not in self._allowed:
            return False
        if tool_name in self._blocked:
            return False
        if self._callback is not None:
            return self._callback(tool_name, args)
        return True
