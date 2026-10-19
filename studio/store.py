import asyncio
import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .actions import apply_action, element_fingerprint
from .agent_store import AgentStore


class VersionConflict(Exception):
    pass


class StudioStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.database_path = path
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.lock = threading.RLock()
        self.listeners: dict[str, dict[Any, asyncio.AbstractEventLoop]] = {}
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS documents (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, version INTEGER NOT NULL,
                elements TEXT NOT NULL, selection TEXT NOT NULL,
                undo_history TEXT NOT NULL, redo_history TEXT NOT NULL,
                created_at REAL NOT NULL, updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id),
                title TEXT NOT NULL, created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
                role TEXT NOT NULL, content TEXT NOT NULL, task_id TEXT,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
                status TEXT NOT NULL, summary TEXT NOT NULL, error TEXT NOT NULL,
                latency_ms REAL NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS tool_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL REFERENCES tasks(id),
                agent_id TEXT NOT NULL, round_number INTEGER NOT NULL,
                kind TEXT NOT NULL, payload TEXT NOT NULL, created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, document_id TEXT NOT NULL REFERENCES documents(id),
                type TEXT NOT NULL, payload TEXT NOT NULL, created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_tasks_session ON tasks(session_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_events_document ON events(document_id, seq);
            CREATE INDEX IF NOT EXISTS idx_tool_logs_task ON tool_logs(task_id, id);
            CREATE TABLE IF NOT EXISTS model_settings (
                id INTEGER PRIMARY KEY CHECK(id=1), config TEXT NOT NULL,
                encrypted_key TEXT NOT NULL, revision INTEGER NOT NULL, updated_at REAL NOT NULL
            );
        """)
        self.connection.commit()
        self.agents = AgentStore(self)

    def close(self) -> None:
        with self.lock:
            self.connection.close()

    def get_model_settings(self) -> dict[str, Any] | None:
        with self.lock:
            row = self.connection.execute("SELECT * FROM model_settings WHERE id=1").fetchone()
        return {**dict(row), "config": json.loads(row["config"])} if row else None

    def save_model_settings(self, config: dict, encrypted_key: str, expected_revision: int) -> None:
        with self.lock, self.connection:
            row = self.connection.execute("SELECT revision FROM model_settings WHERE id=1").fetchone()
            revision = row["revision"] if row else 0
            if revision != expected_revision:
                raise VersionConflict("模型设置已在其他页面更新，请刷新后重试。")
            self.connection.execute(
                "INSERT INTO model_settings VALUES(1,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                "config=excluded.config, encrypted_key=excluded.encrypted_key, "
                "revision=excluded.revision, updated_at=excluded.updated_at",
                (self._json(config), encrypted_key, revision + 1, time.time()),
            )

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _document(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"], "title": row["title"], "version": row["version"],
            "elements": json.loads(row["elements"]),
            "selection": json.loads(row["selection"]),
            "updated_at": row["updated_at"],
        }

    def create_session(self, title: str = "未命名画布") -> dict[str, Any]:
        document_id, session_id = uuid.uuid4().hex, uuid.uuid4().hex
        now = time.time()
        with self.lock, self.connection:
            self.connection.execute(
                "INSERT INTO documents VALUES (?, ?, 0, '[]', '[]', '[]', '[]', ?, ?)",
                (document_id, title, now, now),
            )
            self.connection.execute(
                "INSERT INTO sessions VALUES (?, ?, ?, ?)",
                (session_id, document_id, title, now),
            )
        return self.get_session(session_id)

    def list_sessions(self) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.connection.execute(
                "SELECT s.id, s.document_id, s.title, s.created_at, d.updated_at "
                "FROM sessions s JOIN documents d ON d.id=s.document_id ORDER BY d.updated_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def rename_session(self, session_id: str, title: str) -> dict[str, str]:
        title = title.strip()
        if not title or len(title) > 120:
            raise ValueError("画布名称应为 1 至 120 个字符。")
        with self.lock, self.connection:
            session = self.connection.execute("SELECT document_id FROM sessions WHERE id=?", (session_id,)).fetchone()
            if session is None:
                raise KeyError(f"Session not found: {session_id}")
            document_id = session["document_id"]
            self.connection.execute("UPDATE sessions SET title=? WHERE id=?", (title, session_id))
            self.connection.execute("UPDATE documents SET title=?,updated_at=? WHERE id=?", (title, time.time(), document_id))
        result = {"id": session_id, "document_id": document_id, "title": title}
        self.publish(document_id, "session", result)
        return result

    def get_document(self, document_id: str) -> dict[str, Any]:
        with self.lock:
            row = self.connection.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
        if row is None:
            raise KeyError(f"Document not found: {document_id}")
        return self._document(row)

    def session_document_id(self, session_id: str) -> str:
        with self.lock:
            row = self.connection.execute("SELECT document_id FROM sessions WHERE id=?", (session_id,)).fetchone()
        if row is None:
            raise KeyError(f"Session not found: {session_id}")
        return row["document_id"]

    def delete_session(self, session_id: str) -> dict[str, str]:
        with self.lock, self.connection:
            session = self.connection.execute("SELECT document_id FROM sessions WHERE id=?", (session_id,)).fetchone()
            if session is None:
                raise KeyError(f"Session not found: {session_id}")
            document_id = session["document_id"]
            seq = self.connection.execute(
                "SELECT COALESCE(MAX(seq),0)+1 FROM events WHERE document_id=?", (document_id,),
            ).fetchone()[0]
            self.connection.execute(
                "DELETE FROM tool_logs WHERE task_id IN (SELECT id FROM tasks WHERE session_id=?)", (session_id,),
            )
            self.connection.execute("DELETE FROM messages WHERE session_id=?", (session_id,))
            self.connection.execute("DELETE FROM tasks WHERE session_id=?", (session_id,))
            self.connection.execute("DELETE FROM sessions WHERE id=?", (session_id,))
            self.connection.execute("DELETE FROM events WHERE document_id=?", (document_id,))
            self.connection.execute("DELETE FROM documents WHERE id=?", (document_id,))
            listeners = tuple(self.listeners.get(document_id, {}).items())
        result = {"id": session_id, "document_id": document_id}
        event = {"seq": seq, "document_id": document_id, "type": "session_deleted", "payload": result}
        for queue, loop in listeners:
            loop.call_soon_threadsafe(queue.put_nowait, event)
        return result

    def get_session(self, session_id: str) -> dict[str, Any]:
        with self.lock:
            session = self.connection.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
            if session is None:
                raise KeyError(f"Session not found: {session_id}")
            messages = self.connection.execute(
                "SELECT id, role, content, task_id, created_at FROM messages WHERE session_id=? ORDER BY created_at, rowid",
                (session_id,),
            ).fetchall()
            tasks = self.connection.execute(
                "SELECT id, status, summary, error, latency_ms, created_at, updated_at "
                "FROM tasks WHERE session_id=? ORDER BY created_at DESC LIMIT 20",
                (session_id,),
            ).fetchall()
            document = self.get_document(session["document_id"])
            event_seq = self.connection.execute(
                "SELECT COALESCE(MAX(seq),0) FROM events WHERE document_id=?", (session["document_id"],),
            ).fetchone()[0]
        return {
            "id": session_id, "title": session["title"],
            "document": document, "event_seq": event_seq,
            "messages": [dict(row) for row in messages],
            "tasks": [dict(row) for row in tasks],
        }

    def add_message(self, session_id: str, role: str, content: str, task_id: str | None = None) -> dict[str, Any]:
        message = {
            "id": uuid.uuid4().hex, "role": role, "content": content,
            "task_id": task_id, "created_at": time.time(),
        }
        with self.lock, self.connection:
            self.connection.execute(
                "INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?)",
                (message["id"], session_id, role, content, task_id, message["created_at"]),
            )
        return message

    def context_messages(self, session_id: str, task_id: str) -> list[dict[str, Any]]:
        with self.lock:
            task = self.connection.execute("SELECT rowid,session_id FROM tasks WHERE id=?", (task_id,)).fetchone()
            if task is None or task["session_id"] != session_id:
                raise KeyError("Task does not belong to this session")
            rows = self.connection.execute(
                "SELECT m.role,m.content FROM messages m JOIN tasks t ON t.id=m.task_id "
                "WHERE m.session_id=? AND t.session_id=? AND t.rowid<=? "
                "ORDER BY t.rowid DESC,m.created_at DESC,m.rowid DESC LIMIT 10",
                (session_id, session_id, task["rowid"]),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def create_task(self, session_id: str) -> dict[str, Any]:
        task = {"id": uuid.uuid4().hex, "status": "queued", "summary": "", "error": "",
                "latency_ms": 0, "created_at": time.time(), "updated_at": time.time()}
        with self.lock, self.connection:
            self.connection.execute(
                "INSERT INTO tasks VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (task["id"], session_id, task["status"], "", "", 0, task["created_at"], task["updated_at"]),
            )
        return task

    def update_task(self, task_id: str, status: str, summary: str = "", error: str = "", latency_ms: float = 0) -> None:
        with self.lock, self.connection:
            cursor = self.connection.execute(
                "UPDATE tasks SET status=?, summary=?, error=?, latency_ms=?, updated_at=? WHERE id=?",
                (status, summary, error, latency_ms, time.time(), task_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"Task not found: {task_id}")

    def get_task(self, task_id: str) -> dict[str, Any]:
        with self.lock:
            row = self.connection.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(f"Task not found: {task_id}")
        return dict(row)

    def log_tool(self, task_id: str, agent_id: str, round_number: int, kind: str, payload: Any) -> None:
        with self.lock, self.connection:
            self.connection.execute(
                "INSERT INTO tool_logs(task_id,agent_id,round_number,kind,payload,created_at) VALUES(?,?,?,?,?,?)",
                (task_id, agent_id, round_number, kind, self._json(payload), time.time()),
            )

    def tool_logs(self, task_id: str) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.connection.execute(
                "SELECT id, agent_id, round_number, kind, payload, created_at FROM tool_logs "
                "WHERE task_id=? ORDER BY id", (task_id,),
            ).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]

    def publish(self, document_id: str, event_type: str, payload: Any) -> dict[str, Any]:
        with self.lock, self.connection:
            cursor = self.connection.execute(
                "INSERT INTO events(document_id,type,payload,created_at) VALUES(?,?,?,?)",
                (document_id, event_type, self._json(payload), time.time()),
            )
            event = {"seq": cursor.lastrowid, "document_id": document_id, "type": event_type, "payload": payload}
            listeners = tuple(self.listeners.get(document_id, {}).items())
        for queue, loop in listeners:
            loop.call_soon_threadsafe(queue.put_nowait, event)
        return event

    def events_after(self, document_id: str, seq: int) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.connection.execute(
                "SELECT seq,type,payload FROM events WHERE document_id=? AND seq>? ORDER BY seq LIMIT 500",
                (document_id, seq),
            ).fetchall()
        return [{"seq": row["seq"], "document_id": document_id, "type": row["type"],
                 "payload": json.loads(row["payload"])} for row in rows]

    def subscribe(self, document_id: str, queue: Any) -> None:
        with self.lock:
            self.listeners.setdefault(document_id, {})[queue] = asyncio.get_running_loop()

    def unsubscribe(self, document_id: str, queue: Any) -> None:
        with self.lock:
            listeners = self.listeners.get(document_id)
            if listeners is not None:
                listeners.pop(queue, None)
                if not listeners:
                    self.listeners.pop(document_id)

    def _save(self, row: sqlite3.Row, elements: list[dict], selection: list[str], record_undo: bool) -> dict[str, Any]:
        prior_elements = json.loads(row["elements"])
        prior_selection = json.loads(row["selection"])
        if prior_elements == elements and prior_selection == selection:
            return self._document(row)
        if len(elements) > 3000 or len(self._json(elements)) > 8_000_000:
            raise ValueError("Canvas size limit exceeded")
        undo = json.loads(row["undo_history"])
        redo = json.loads(row["redo_history"])
        ignored_metadata = {"version", "versionNonce", "updated", "index"}
        prior_content = [{key: value for key, value in element.items() if key not in ignored_metadata}
                         for element in prior_elements]
        next_content = [{key: value for key, value in element.items() if key not in ignored_metadata}
                        for element in elements]
        if prior_content == next_content and prior_selection == selection:
            now = time.time()
            self.connection.execute(
                "UPDATE documents SET elements=?, updated_at=? WHERE id=?",
                (self._json(elements), now, row["id"]),
            )
            return {"id": row["id"], "title": row["title"], "version": row["version"],
                    "elements": elements, "selection": selection, "updated_at": now}
        if record_undo and prior_content != next_content:
            undo = (undo + [{"elements": prior_elements, "selection": prior_selection}])[-50:]
            redo = []
        now = time.time()
        self.connection.execute(
            "UPDATE documents SET version=?, elements=?, selection=?, undo_history=?, redo_history=?, updated_at=? WHERE id=?",
            (row["version"] + 1, self._json(elements), self._json(selection),
             self._json(undo), self._json(redo), now, row["id"]),
        )
        return {"id": row["id"], "title": row["title"], "version": row["version"] + 1,
                "elements": elements, "selection": selection, "updated_at": now}

    def save_snapshot(self, document_id: str, version: int, elements: list[dict], selection: list[str], client_id: str = "") -> dict[str, Any]:
        ids = [element.get("id") for element in elements]
        if any(not isinstance(element_id, str) for element_id in ids) or len(ids) != len(set(ids)):
            raise ValueError("Canvas elements need unique IDs")
        if any(element.get("type") not in {"rectangle", "ellipse", "diamond", "text", "line", "arrow", "freedraw"} for element in elements):
            raise ValueError("Unsupported Excalidraw element")
        if set(selection) - set(ids):
            raise ValueError("Selection refers to missing elements")
        with self.lock, self.connection:
            row = self.connection.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
            if row is None:
                raise KeyError(f"Document not found: {document_id}")
            if row["version"] != version:
                raise VersionConflict(f"Canvas changed from revision {version} to {row['version']}")
            document = self._save(row, elements, selection, True)
        if document["version"] != version:
            self.publish(document_id, "scene", {"document": document, "source": "manual", "client_id": client_id})
        return document

    def commit_action(self, document_id: str, action: dict[str, Any], expected_elements: dict[str, Any],
                      receipt: tuple[str, int] | None = None) -> dict[str, Any]:
        with self.lock, self.connection:
            row = self.connection.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
            if row is None:
                raise KeyError(f"Document not found: {document_id}")
            if receipt is not None:
                saved, duplicate = self.agents.validate_commit(document_id, *receipt)
                if duplicate:
                    return {"document": self._document(row), "changed_ids": [], "already_committed": True}
                if saved["action"] != action:
                    raise ValueError("Drawing action does not match the saved result")
                expected_elements = saved["expected_elements"]
            elements = json.loads(row["elements"])
            current_by_id = {element["id"]: element for element in elements}
            for element_id, expected in expected_elements.items():
                element = current_by_id.get(element_id)
                if element is None:
                    raise VersionConflict(f"Element {element_id} changed while the Agent was drawing")
                if isinstance(expected, dict):
                    matches = element_fingerprint(element) == expected["fingerprint"]
                else:
                    matches = int(element.get("version", 1)) == expected
                if not matches:
                    raise VersionConflict(f"Element {element_id} changed while the Agent was drawing")
            updated, selection, changed = apply_action(elements, json.loads(row["selection"]), action)
            document = self._save(row, updated, selection, True)
            if receipt is not None:
                self.agents.advance_commit(*receipt)
        self.publish(document_id, "scene", {"document": document, "source": "agent", "action": action["kind"]})
        return {"document": document, "changed_ids": changed}

    def history_step(self, document_id: str, direction: str) -> dict[str, Any]:
        if direction not in {"undo", "redo"}:
            raise ValueError("Unknown history direction")
        with self.lock, self.connection:
            row = self.connection.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
            if row is None:
                raise KeyError(f"Document not found: {document_id}")
            undo = json.loads(row["undo_history"])
            redo = json.loads(row["redo_history"])
            source = undo if direction == "undo" else redo
            if not source:
                return self._document(row)
            target = source.pop()
            current = {"elements": json.loads(row["elements"]), "selection": json.loads(row["selection"])}
            if direction == "undo":
                redo.append(current)
            else:
                undo.append(current)
            now = time.time()
            self.connection.execute(
                "UPDATE documents SET version=?, elements=?, selection=?, undo_history=?, redo_history=?, updated_at=? WHERE id=?",
                (row["version"] + 1, self._json(target["elements"]), self._json(target["selection"]),
                 self._json(undo[-50:]), self._json(redo[-50:]), now, document_id),
            )
            document = {"id": document_id, "title": row["title"], "version": row["version"] + 1,
                        "elements": target["elements"], "selection": target["selection"], "updated_at": now}
        self.publish(document_id, "scene", {"document": document, "source": direction})
        return document
