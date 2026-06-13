import os
import json
import time

RUNS_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "runs"))


class SessionManager:
    """Persistent conversation history across agent runs.

    Each session lives in ``runs/sessions/<session_id>/`` with:
      meta.json       — creation time, provider, api_key
      history.jsonl   — user & assistant messages, one JSON object per line
    """

    def __init__(self):
        self._sessions_dir = os.path.join(RUNS_DIR, "sessions")
        os.makedirs(self._sessions_dir, exist_ok=True)

    def _session_dir(self, session_id: str) -> str:
        return os.path.join(self._sessions_dir, session_id)

    def _meta_path(self, session_id: str) -> str:
        return os.path.join(self._sessions_dir, session_id, "meta.json")

    def _history_path(self, session_id: str) -> str:
        return os.path.join(self._sessions_dir, session_id, "history.jsonl")

    # ── session lifecycle ─────────────────────────────────────────────

    def create(self, provider: str = "openai", api_key: str = "") -> str:
        """Create a new session and return its id."""
        import random
        import string
        ts = time.strftime("%y%m%d-%H%M%S", time.localtime())
        rand = "".join(random.choices(string.digits, k=6))
        session_id = f"{ts}-{rand}"

        sdir = self._session_dir(session_id)
        os.makedirs(sdir, exist_ok=True)

        with open(self._meta_path(session_id), "w", encoding="utf-8") as f:
            json.dump({
                "session_id": session_id,
                "provider": provider,
                "api_key": api_key,
                "created_at": time.time(),
            }, f, ensure_ascii=False, indent=2)

        return session_id

    def get_session(self, session_id: str) -> dict | None:
        path = self._meta_path(session_id)
        if not os.path.isfile(path):
            return None
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    # ── history ───────────────────────────────────────────────────────

    def get_history(self, session_id: str) -> list[dict]:
        """Return all past messages as ``{"role": ..., "content": ...}``."""
        path = self._history_path(session_id)
        if not os.path.isfile(path):
            return []
        messages = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if not stripped:
                    continue
                msg = json.loads(stripped)
                messages.append({
                    "role": msg["role"],
                    "content": msg.get("content", ""),
                })
        return messages

    def append_message(self, session_id: str, role: str, content: str,
                       tool_calls: list | None = None):
        """Persist a single message to the session's history.jsonl."""
        entry = {
            "role": role,
            "content": content,
            "timestamp": time.time(),
        }
        if tool_calls:
            entry["tool_calls"] = tool_calls

        path = self._history_path(session_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
