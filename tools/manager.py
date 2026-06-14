import asyncio
import uuid
import os
import json
from typing import Callable
from .policy import ToolPolicy, PolicyDecision

PERSISTENT_CACHE_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "runs", "permission_cache.json")
)


class PermissionManager:
    """Async permission gate with two-layer decision cache.

    * Delegates synchronous evaluation to ``policy.evaluate()``.
    * On ``ASK``: emits ``permission.requested``, waits for frontend via
      ``asyncio.Future``.
    * After user responds, ``_apply_response()`` writes to:
      - **Session cache** — ``(tool_name, frozen_args) → bool`` (same session)
      - **Persistent cache** — ``tool_name → bool`` (across daemon restarts)
        only written for "always allow" / "always deny" choices.
    * **Boundary invariant**: if ``policy.matches_outside_cwd()`` returns
      ``True`` (bash CWD escape), all caches are **bypassed** and the user
      is always asked.  This guarantees that even a long-trusted ``bash``
      tool still triggers approval for dangerous commands.
    """

    def __init__(self,
                 policy: ToolPolicy | None = None,
                 on_request: Callable[[str, str, dict], None] | None = None):
        self.policy = policy or ToolPolicy()
        self._on_request = on_request
        self._pending: dict[str, asyncio.Future] = {}

        # session cache:  (tool_name, frozen_args)  →  bool
        self._session_cache: dict[tuple, bool] = {}

        # persistent cache:  tool_name  →  bool
        self._persistent_cache: dict[str, bool] = {}
        self._load_persistent_cache()

    # ── core check ─────────────────────────────────────────────────────

    async def check_and_wait(self, tool_name: str, args: dict,
                             timeout: float = 300.0) -> tuple[bool, str]:
        """Evaluate policy, asking the user if the decision is ``ASK``.

        Cache bypass: tools matching ``matches_outside_cwd()`` always
        require user approval.

        Returns
        -------
        ``(approved, extra)``
        """
        # 1. Synchronous policy evaluation
        decision = self.policy.evaluate(tool_name, args)

        if decision == PolicyDecision.ALLOW:
            return True, ""

        if decision == PolicyDecision.DENY:
            return False, f"Tool '{tool_name}' is denied by policy"

        # 2. Outside-CWD boundary — NEVER cached, always ask
        if tool_name == "bash":
            command = args.get("command", "")
            if self.policy.matches_outside_cwd(command):
                return await self._do_ask(tool_name, args, timeout)

        # 3. Session cache (once decisions)
        cache_key = (tool_name, self._freeze_args(args))
        cached = self._session_cache.get(cache_key)
        if cached is not None:
            return cached, ""

        # 4. Persistent cache (always decisions)
        persistent = self._persistent_cache.get(tool_name)
        if persistent is not None:
            self._session_cache[cache_key] = persistent
            return persistent, ""

        # 5. ASK — wait for user
        return await self._do_ask(tool_name, args, timeout)

    # ── ask user / respond ─────────────────────────────────────────────

    async def _do_ask(self, tool_name: str, args: dict,
                      timeout: float) -> tuple[bool, str]:
        req_id = str(uuid.uuid4())
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[req_id] = future

        if self._on_request:
            self._on_request(req_id, tool_name, args)

        try:
            # payload: {"decision": True/False, "response_type": "once"/"always"}
            payload = await asyncio.wait_for(future, timeout=timeout)
            approved = payload["decision"]
            response_type = payload.get("response_type", "once")
            self._apply_response(approved, response_type, tool_name, args)
            return approved, req_id
        except asyncio.TimeoutError:
            self._session_cache[(tool_name, self._freeze_args(args))] = False
            return False, req_id
        finally:
            self._pending.pop(req_id, None)

    def respond(self, request_id: str, approved: bool,
                response_type: str = "once") -> bool:
        """Resolve a pending permission request.

        Called by the daemon when the frontend sends ``permission_respond``.

        ``response_type`` is ``"once"`` | ``"always"`` — when ``"always"``,
        the decision is also written to the persistent cache.
        """
        future = self._pending.get(request_id)
        if future is not None and not future.done():
            future.set_result({
                "decision": approved,
                "response_type": response_type,
            })
            return True
        return False

    def _apply_response(self, approved: bool, response_type: str,
                        tool_name: str, args: dict):
        """Write decision to session cache, and persistent cache if always."""
        cache_key = (tool_name, self._freeze_args(args))

        # always write to session cache
        self._session_cache[cache_key] = approved

        if response_type == "always":
            self._persistent_cache[tool_name] = approved
            self._save_persistent_cache()

    # ── decision caches ────────────────────────────────────────────────

    def _freeze_args(self, args: dict) -> frozenset:
        return frozenset((k, str(v)) for k, v in sorted(args.items()))

    def clear_session_cache(self):
        self._session_cache.clear()

    def invalidate_session_cache(self, tool_name: str):
        self._session_cache = {
            k: v for k, v in self._session_cache.items()
            if k[0] != tool_name
        }

    # ── persistent cache (JSON file) ──────────────────────────────────

    def _load_persistent_cache(self):
        try:
            if os.path.isfile(PERSISTENT_CACHE_PATH):
                with open(PERSISTENT_CACHE_PATH, encoding="utf-8") as f:
                    data = json.load(f)
                    self._persistent_cache = data.get("decisions", {})
        except Exception:
            self._persistent_cache = {}

    def _save_persistent_cache(self):
        try:
            os.makedirs(os.path.dirname(PERSISTENT_CACHE_PATH), exist_ok=True)
            with open(PERSISTENT_CACHE_PATH, "w", encoding="utf-8") as f:
                json.dump({"decisions": self._persistent_cache}, f,
                          ensure_ascii=False, indent=2)
        except Exception:
            pass

    def clear_persistent_cache(self):
        self._persistent_cache.clear()
        self._save_persistent_cache()
