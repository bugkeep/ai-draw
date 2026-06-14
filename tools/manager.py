import asyncio
import uuid
from typing import Callable
from .policy import ToolPolicy, PolicyDecision


class PermissionManager:
    """Async permission gate built on top of ``ToolPolicy``.

    * Delegates synchronous evaluation to ``policy.evaluate()``.
    * On ``ASK`` decisions: emits a ``permission.requested`` event,
      creates an ``asyncio.Future``, and waits for the frontend to
      call ``respond()``.
    * Approved decisions are cached so repeated identical tool calls
      are auto-allowed during the same session.
    """

    def __init__(self,
                 policy: ToolPolicy | None = None,
                 on_request: Callable[[str, str, dict], None] | None = None):
        self.policy = policy or ToolPolicy()
        self._on_request = on_request
        self._pending: dict[str, asyncio.Future] = {}
        # cache:  (tool_name, frozenset(sorted(args.items())))  →  bool
        self._cache: dict[tuple, bool] = {}

    # ── core check ─────────────────────────────────────────────────────

    async def check_and_wait(self, tool_name: str, args: dict,
                             timeout: float = 300.0) -> tuple[bool, str]:
        """Evaluate policy, asking the user if the decision is ``ASK``.

        Returns
        -------
        ``(approved, extra)``

        * ``ALLOW``    → ``(True, "")``
        * ``DENY``     → ``(False, "reason string")``
        * ``ASK``      → ``(True/False, request_id)``
        """
        # 1. Decision cache hit
        cache_key = (tool_name, self._freeze_args(args))
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached, ""

        # 2. Synchronous policy evaluation
        decision = self.policy.evaluate(tool_name, args)

        if decision == PolicyDecision.ALLOW:
            return True, ""

        if decision == PolicyDecision.DENY:
            return False, f"Tool '{tool_name}' is denied by policy"

        # 3. ASK — wait for user
        req_id = str(uuid.uuid4())
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[req_id] = future

        if self._on_request:
            self._on_request(req_id, tool_name, args)

        try:
            approved = await asyncio.wait_for(future, timeout=timeout)
            # cache so the same call skips approval next time
            self._cache[cache_key] = approved
            return approved, req_id
        except asyncio.TimeoutError:
            self._cache[cache_key] = False
            return False, req_id
        finally:
            self._pending.pop(req_id, None)

    def respond(self, request_id: str, approved: bool) -> bool:
        """Resolve a pending permission request (called by the daemon)."""
        future = self._pending.get(request_id)
        if future is not None and not future.done():
            future.set_result(approved)
            return True
        return False

    # ── decision cache ─────────────────────────────────────────────────

    def _freeze_args(self, args: dict) -> frozenset:
        return frozenset((k, str(v)) for k, v in sorted(args.items()))

    def clear_cache(self):
        self._cache.clear()

    def invalidate_cache(self, tool_name: str):
        """Remove cached decisions for a specific tool."""
        self._cache = {
            k: v for k, v in self._cache.items()
            if k[0] != tool_name
        }
