import asyncio
import uuid
from typing import Callable


class PermissionChecker:
    """Tool-call permission gate with asynchronous user-approval support.

    Evaluation order:
      1. Allowlist check  — if set, only listed tools pass
      2. Blocklist check   — blocked tools are denied immediately
      3. Callback check    — optional sync callback can approve/deny/undecide
      4. User approval     — if ``approval_required`` is set and still
                             undecided, emits ``permission.requested``
                             and waits for the frontend to respond

    ``_pending`` maps request IDs to ``asyncio.Future[bool]``.  The daemon
    receives the user's response via ``permission.respond`` and calls
    ``respond()`` to resolve the future.
    """

    def __init__(self,
                 approval_required: bool = False,
                 on_request: Callable[[str, str, dict], None] | None = None):
        self._blocked: set[str] = set()
        self._allowed: set[str] | None = None  # None = all-allowed mode
        self._callback: Callable[[str, dict], bool | None] | None = None
        self._approval_required = approval_required
        self._pending: dict[str, asyncio.Future] = {}
        self._on_request = on_request

    # ── configuration ──────────────────────────────────────────────────

    @property
    def approval_required(self) -> bool:
        return self._approval_required

    @approval_required.setter
    def approval_required(self, val: bool):
        self._approval_required = val

    def block(self, *tool_names: str):
        self._blocked.update(tool_names)

    def unblock(self, *tool_names: str):
        self._blocked.difference_update(tool_names)

    def allow_only(self, *tool_names: str):
        self._allowed = set(tool_names) if tool_names else None

    def set_callback(self, cb: Callable[[str, dict], bool | None] | None):
        """Set a custom callback that returns ``True``/``False``/``None``."""
        self._callback = cb

    # ── synchronous pre-check ──────────────────────────────────────────

    def _pre_check(self, tool_name: str, args: dict) -> bool | None:
        """Return ``True`` (allowed), ``False`` (denied), or ``None`` (undecided).

        ``None`` when no rule matches and user approval is required.
        Without ``approval_required`` the default is ``True`` (allowed).
        """
        if self._allowed is not None and tool_name not in self._allowed:
            return False
        if tool_name in self._blocked:
            return False
        if self._callback is not None:
            return self._callback(tool_name, args)
        if self._approval_required:
            return None  # undecided → ask user
        # No rules → allow
        return True

    # ── async check-and-wait ───────────────────────────────────────────

    async def check_and_wait(self, tool_name: str, args: dict,
                             timeout: float = 300.0) -> tuple[bool, str]:
        """Check permission, asking the user if undecided.

        Returns ``(approved, request_id_or_reason)``.  When denied by rule,
        ``request_id_or_reason`` is the reason string.  When pending user
        approval, it contains the ``request_id`` (use to resolve later).
        """
        result = self._pre_check(tool_name, args)
        if result is not None:
            return result, "" if result else f"Tool '{tool_name}' is blocked"

        # ── need user approval ──────────────────────────────────────────
        req_id = str(uuid.uuid4())
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[req_id] = future

        if self._on_request:
            self._on_request(req_id, tool_name, args)

        try:
            approved = await asyncio.wait_for(future, timeout=timeout)
            return approved, req_id
        except asyncio.TimeoutError:
            return False, req_id
        finally:
            self._pending.pop(req_id, None)

    def respond(self, request_id: str, approved: bool) -> bool:
        """Resolve a pending permission request.  Returns True if found."""
        future = self._pending.get(request_id)
        if future is not None and not future.done():
            future.set_result(approved)
            return True
        return False
