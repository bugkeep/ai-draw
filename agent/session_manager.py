import os
import json
import time
from datetime import datetime
from typing import Any

RUNS_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "runs"))

# Truncate tool result content to this many chars when read back into memory.
# The original thread.jsonl on disk is NOT modified.
_TRUNCATE_TOOL_RESULT_CHARS = 2000


class SessionStore:
    """Read/write session data (thread + notes) from the filesystem.

    ``read_messages()`` returns messages in OpenAI API format — ready to be
    sent to the LLM.  Each line in ``thread.jsonl`` stores exactly the
    fields the API expects:

      user:      {"role": "user",      "content": "..."}
      assistant: {"role": "assistant", "content": "...", "tool_calls": [...]}
      tool:      {"role": "tool",      "content": "...", "tool_call_id": "..."}

    ``notes.md`` holds long-term facts / decisions the agent has chosen to
    remember across runs.
    """

    def __init__(self, session_id: str, sessions_dir: str):
        self._session_dir = os.path.join(sessions_dir, session_id)
        self._thread_path = os.path.join(self._session_dir, "thread.jsonl")
        self._notes_path = os.path.join(self._session_dir, "notes.md")

    # ── thread ─────────────────────────────────────────────────────────

    def read_messages(self, truncate_tool_result: bool = True,
                       max_tool_chars: int = 0) -> list[dict]:
        """Return stored messages, optionally truncating large tool results.

        ``truncate_tool_result`` — when True, tool-role content longer than
        ``max_tool_chars`` is truncated in the returned list (the original
        ``thread.jsonl`` on disk is NOT modified).

        If ``max_tool_chars`` is 0 (default), uses
        ``_TRUNCATE_TOOL_RESULT_CHARS``.
        """
        if not os.path.isfile(self._thread_path):
            return []
        max_chars = max_tool_chars or _TRUNCATE_TOOL_RESULT_CHARS
        msgs: list[dict] = []
        with open(self._thread_path, encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if s:
                    msg = json.loads(s)
                    if truncate_tool_result and msg.get("role") == "tool":
                        content = msg.get("content", "")
                        if isinstance(content, str) and len(content) > max_chars:
                            msg = dict(msg)
                            msg["content"] = content[:max_chars] + f"\n... (truncated {len(content) - max_chars} chars)"
                    msgs.append(msg)
        return msgs

    def append_message(self, role: str, content: str = "",
                       tool_calls: list | None = None,
                       tool_call_id: str = ""):
        """Append a single message to thread.jsonl."""
        entry: dict[str, Any] = {"role": role}
        if content:
            entry["content"] = content
        if tool_calls:
            entry["tool_calls"] = tool_calls
        if tool_call_id:
            entry["tool_call_id"] = tool_call_id
        self._write_entry(entry)

    def append_messages(self, messages: list[dict]):
        """Append multiple messages to thread.jsonl in one write batch."""
        if not messages:
            return
        os.makedirs(self._session_dir, exist_ok=True)
        with open(self._thread_path, "a", encoding="utf-8") as f:
            for msg in messages:
                entry: dict[str, Any] = {"role": msg["role"]}
                if msg.get("content"):
                    entry["content"] = msg["content"]
                if msg.get("tool_calls"):
                    entry["tool_calls"] = msg["tool_calls"]
                if msg.get("tool_call_id"):
                    entry["tool_call_id"] = msg["tool_call_id"]
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # ── notes ──────────────────────────────────────────────────────────

    def read_notes(self) -> str:
        """Return notes.md content, or '' if file doesn't exist."""
        if not os.path.isfile(self._notes_path):
            return ""
        with open(self._notes_path, encoding="utf-8") as f:
            return f.read().strip()

    def append_notes(self, text: str):
        """Append to notes.md."""
        os.makedirs(self._session_dir, exist_ok=True)
        with open(self._notes_path, "a", encoding="utf-8") as f:
            if os.path.getsize(self._notes_path) > 0:
                f.write("\n")
            f.write(text.strip() + "\n")

    # ── internal ───────────────────────────────────────────────────────

    def _write_entry(self, entry: dict):
        os.makedirs(self._session_dir, exist_ok=True)
        with open(self._thread_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


class SessionManager:
    """Persistent conversation history across agent runs.

    Each session lives in ``runs/sessions/<session_id>/`` with:
      meta.json       — session profile (id, mode, status, title,
                        timestamps, run list, provider config)
      thread.jsonl    — user / assistant / tool messages (API-ready)
      notes.md        — long-term facts & decisions saved by the agent
    """

    def __init__(self):
        self._sessions_dir = os.path.join(RUNS_DIR, "sessions")
        os.makedirs(self._sessions_dir, exist_ok=True)

    def _session_dir(self, session_id: str) -> str:
        return os.path.join(self._sessions_dir, session_id)

    def _meta_path(self, session_id: str) -> str:
        return os.path.join(self._sessions_dir, session_id, "meta.json")

    def _thread_path(self, session_id: str) -> str:
        return os.path.join(self._sessions_dir, session_id, "thread.jsonl")

    def store(self, session_id: str) -> SessionStore:
        """Create a SessionStore bound to this session."""
        return SessionStore(session_id, self._sessions_dir)

    @staticmethod
    def _now() -> str:
        return datetime.now().isoformat(timespec="seconds")

    @staticmethod
    def _new_id() -> str:
        import random
        import string
        ts = time.strftime("%y%m%d-%H%M%S", time.localtime())
        rand = "".join(random.choices(string.digits, k=6))
        return f"sess-{ts}-{rand}"

    # ── session lifecycle ─────────────────────────────────────────────

    def create(self, mode: str = "agent", title: str = "",
               provider: str = "openai", api_key: str = "") -> str:
        """Create a new session and return its id."""
        session_id = self._new_id()
        sdir = self._session_dir(session_id)
        os.makedirs(sdir, exist_ok=True)

        now = self._now()
        meta = {
            "id": session_id,
            "mode": mode,
            "status": "active",
            "title": title,
            "provider": provider,
            "api_key": api_key,
            "created_at": now,
            "updated_at": now,
            "runs": [],
        }
        with open(self._meta_path(session_id), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

        return session_id

    def get_session(self, session_id: str) -> dict | None:
        path = self._meta_path(session_id)
        if not os.path.isfile(path):
            return None
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def start_run(self, session_id: str, run_id: str) -> dict | None:
        """Record a run under this session and return updated meta, or None."""
        meta = self.get_session(session_id)
        if meta is None:
            return None
        if run_id not in meta["runs"]:
            meta["runs"].append(run_id)
        meta["updated_at"] = self._now()
        with open(self._meta_path(session_id), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        return meta

    # ── backward-compat: read / append thread.jsonl ────────────────────

    def get_history(self, session_id: str) -> list[dict]:
        """Return all past messages (backward compat, reads thread.jsonl)."""
        return self.store(session_id).read_messages()

    def append_message(self, session_id: str, role: str, content: str,
                       tool_calls: list | None = None):
        """Persist a single message (backward compat, writes thread.jsonl)."""
        self.store(session_id).append_message(role, content, tool_calls)

    def send_message(self, session_id: str, message: str,
                     run_id: str) -> dict:
        """Write user message to thread first, then start the run.

        Returns a dict for the caller to dispatch:
          ``{"session": ..., "store": ..., "run_id": ...}``
        """
        session = self.get_session(session_id)
        if session is None:
            return {"error": f"Session not found: {session_id}"}

        store = self.store(session_id)

        # 1. persist user message immediately
        store.append_message("user", message)

        # 2. record the run in meta
        self.start_run(session_id, run_id)

        return {
            "session": session,
            "store": store,
            "run_id": run_id,
        }
