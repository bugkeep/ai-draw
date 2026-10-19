import json
import time
import uuid
from typing import Any


def stable_id(prefix: str, value: str) -> str:
    return f"{prefix}_{uuid.uuid5(uuid.NAMESPACE_URL, value).hex}"


class AgentStore:
    def __init__(self, studio):
        self.studio = studio
        self.connection = studio.connection
        self.lock = studio.lock
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS agent_sessions (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                nickname TEXT NOT NULL, state TEXT NOT NULL,
                revision INTEGER NOT NULL, summary TEXT NOT NULL,
                owner_task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                created_at REAL NOT NULL, updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS agent_inputs (
                id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL REFERENCES agent_sessions(id) ON DELETE CASCADE,
                task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                message TEXT NOT NULL, relevant_ids TEXT NOT NULL,
                status TEXT NOT NULL, created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS agent_runs (
                id TEXT PRIMARY KEY,
                input_id TEXT NOT NULL UNIQUE REFERENCES agent_inputs(id) ON DELETE CASCADE,
                owner_task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                status TEXT NOT NULL, document TEXT, draft TEXT, selection TEXT,
                prepared TEXT NOT NULL, conversation TEXT NOT NULL, pending_response TEXT,
                error TEXT NOT NULL, summary TEXT NOT NULL, result_id TEXT,
                result_revision INTEGER NOT NULL,
                created_at REAL NOT NULL, updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS agent_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id TEXT NOT NULL REFERENCES agent_sessions(id) ON DELETE CASCADE,
                run_id TEXT NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
                task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                payload TEXT NOT NULL, created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS agent_tool_calls (
                caller_id TEXT NOT NULL, call_id TEXT NOT NULL,
                task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                name TEXT NOT NULL, arguments TEXT NOT NULL, output TEXT,
                created_at REAL NOT NULL,
                PRIMARY KEY(caller_id, call_id)
            );
            CREATE TABLE IF NOT EXISTS drawing_results (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                agent_id TEXT REFERENCES agent_sessions(id) ON DELETE CASCADE,
                run_id TEXT UNIQUE REFERENCES agent_runs(id) ON DELETE CASCADE,
                actions TEXT NOT NULL, depends_on TEXT NOT NULL,
                summary TEXT NOT NULL, committed_count INTEGER NOT NULL,
                rejected_reason TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_agents_session ON agent_sessions(session_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_agent_inputs_queue ON agent_inputs(agent_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_agent_runs_owner ON agent_runs(owner_task_id, status);
            CREATE INDEX IF NOT EXISTS idx_agent_messages_run ON agent_messages(run_id, id);
            CREATE INDEX IF NOT EXISTS idx_drawing_results_session ON drawing_results(session_id, created_at);
        """)
        self.connection.commit()

    @staticmethod
    def encode(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def _agent(self, session_id: str, agent_id: str):
        row = self.connection.execute(
            "SELECT * FROM agent_sessions WHERE id=? AND session_id=?", (agent_id, session_id),
        ).fetchone()
        if row is None:
            raise KeyError("Agent does not belong to this canvas session")
        return row

    def _check_task(self, session_id: str, task_id: str) -> None:
        row = self.connection.execute("SELECT session_id FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None or row["session_id"] != session_id:
            raise KeyError("Task does not belong to this canvas session")

    def check_task(self, session_id: str, task_id: str) -> None:
        with self.lock:
            self._check_task(session_id, task_id)

    def _touch(self, agent_id: str, state: str | None = None) -> None:
        self.connection.execute(
            "UPDATE agent_sessions SET state=COALESCE(?,state),revision=revision+1,updated_at=? WHERE id=?",
            (state, time.time(), agent_id),
        )

    def _enqueue(self, agent_id: str, task_id: str, call_id: str,
                 message: str, relevant_ids: list[str]) -> tuple[str, str]:
        input_id = stable_id("input", f"{task_id}/{call_id}/input")
        run_id = stable_id("run", input_id)
        existing = self.connection.execute("SELECT * FROM agent_inputs WHERE id=?", (input_id,)).fetchone()
        if existing:
            if (existing["agent_id"], existing["message"], existing["relevant_ids"]) != (
                agent_id, message, self.encode(relevant_ids),
            ):
                raise ValueError("A repeated input call must have identical arguments")
            return input_id, run_id
        now = time.time()
        self.connection.execute(
            "INSERT INTO agent_inputs VALUES(?,?,?,?,?,'queued',?)",
            (input_id, agent_id, task_id, message, self.encode(relevant_ids), now),
        )
        self.connection.execute(
            "INSERT INTO agent_runs VALUES(?,?,?,'queued',NULL,NULL,NULL,'[]','[]',NULL,'','',NULL,0,?,?)",
            (run_id, input_id, task_id, now, now),
        )
        return input_id, run_id

    def create_agent(self, session_id: str, task_id: str, call_id: str, message: str,
                     nickname: str, relevant_ids: list[str]) -> dict:
        agent_id = stable_id("agent", f"{task_id}/{call_id}/agent")
        with self.lock, self.connection:
            self._check_task(session_id, task_id)
            row = self.connection.execute("SELECT * FROM agent_sessions WHERE id=?", (agent_id,)).fetchone()
            if row is None:
                now = time.time()
                self.connection.execute(
                    "INSERT INTO agent_sessions VALUES(?,?,?,'Running',1,'',?,?,?)",
                    (agent_id, session_id, nickname, task_id, now, now),
                )
            elif row["session_id"] != session_id or row["nickname"] != nickname:
                raise ValueError("A repeated spawn call must refer to the same Agent")
            input_id, run_id = self._enqueue(agent_id, task_id, call_id, message, relevant_ids)
        return {**self.get_agent(session_id, agent_id), "input_id": input_id, "run_id": run_id}

    def enqueue_input(self, session_id: str, task_id: str, agent_id: str, call_id: str,
                      message: str, relevant_ids: list[str]) -> dict:
        with self.lock, self.connection:
            self._check_task(session_id, task_id)
            row = self._agent(session_id, agent_id)
            if row["state"] == "Shutdown":
                raise ValueError("Resume this Agent before sending a new input")
            input_id = stable_id("input", f"{task_id}/{call_id}/input")
            exists = self.connection.execute("SELECT id FROM agent_inputs WHERE id=?", (input_id,)).fetchone()
            input_id, run_id = self._enqueue(agent_id, task_id, call_id, message, relevant_ids)
            if not exists:
                self.connection.execute("UPDATE agent_sessions SET owner_task_id=? WHERE id=?", (task_id, agent_id))
                self._touch(agent_id, "Running")
        return {**self.get_agent(session_id, agent_id), "input_id": input_id, "run_id": run_id}

    def get_agent(self, session_id: str, agent_id: str) -> dict:
        with self.lock:
            row = self._agent(session_id, agent_id)
            inputs = self.connection.execute(
                "SELECT i.id AS input_id,r.id AS run_id,i.status FROM agent_inputs i "
                "JOIN agent_runs r ON r.input_id=i.id WHERE i.agent_id=? "
                "AND i.status IN ('queued','running','paused') ORDER BY i.rowid", (agent_id,),
            ).fetchall()
            pending = self.connection.execute(
                "SELECT * FROM drawing_results WHERE agent_id=? AND rejected_reason='' "
                "AND committed_count<json_array_length(actions) ORDER BY rowid", (agent_id,),
            ).fetchall()
            latest = self.connection.execute(
                "SELECT r.id FROM agent_runs r JOIN agent_inputs i ON i.id=r.input_id "
                "WHERE i.agent_id=? AND r.status IN ('completed','failed') ORDER BY i.rowid DESC LIMIT 1",
                (agent_id,),
            ).fetchone()
            latest_result = None
            if latest:
                latest_result = self.run_summary(self.get_run(session_id, latest["id"]))
                if latest_result["result_id"]:
                    latest_result.update(self.result_summary(self.get_result(session_id, latest_result["result_id"])))
        return {"agent_id": row["id"], "nickname": row["nickname"], "state": row["state"],
                "revision": row["revision"], "summary": row["summary"],
                "owner_task_id": row["owner_task_id"], "inputs": [dict(item) for item in inputs],
                "latest_result": latest_result,
                "pending_results": [self.result_summary(dict(item)) for item in pending]}

    def list_agents(self, session_id: str) -> list[dict]:
        with self.lock:
            rows = self.connection.execute(
                "SELECT id FROM agent_sessions WHERE session_id=? ORDER BY created_at,rowid", (session_id,),
            ).fetchall()
            return [self.get_agent(session_id, row["id"]) for row in rows]

    def get_run(self, session_id: str, run_id: str) -> dict:
        with self.lock:
            row = self.connection.execute(
                "SELECT r.*,i.agent_id,i.message,i.relevant_ids FROM agent_runs r "
                "JOIN agent_inputs i ON i.id=r.input_id JOIN agent_sessions a ON a.id=i.agent_id "
                "WHERE r.id=? AND a.session_id=?", (run_id, session_id),
            ).fetchone()
        if row is None:
            raise KeyError("Run does not belong to this canvas session")
        result = dict(row)
        for field in ("document", "draft", "selection", "prepared", "conversation", "pending_response", "relevant_ids"):
            result[field] = json.loads(result[field]) if result[field] is not None else None
        return result

    def next_run(self, session_id: str, agent_id: str, task_id: str) -> dict | None:
        with self.lock, self.connection:
            row = self._agent(session_id, agent_id)
            if row["state"] == "Shutdown":
                return None
            candidate = self.connection.execute(
                "SELECT r.id,r.input_id FROM agent_inputs i JOIN agent_runs r ON r.input_id=i.id "
                "WHERE i.agent_id=? AND i.status IN ('running','paused','queued') ORDER BY i.rowid LIMIT 1",
                (agent_id,),
            ).fetchone()
            if candidate is None:
                if row["state"] != "Final":
                    self._touch(agent_id, "Final")
                return None
            self.connection.execute("UPDATE agent_inputs SET status='running' WHERE id=?", (candidate["input_id"],))
            self.connection.execute(
                "UPDATE agent_runs SET status='running',owner_task_id=?,updated_at=? WHERE id=?",
                (task_id, time.time(), candidate["id"]),
            )
        return self.get_run(session_id, candidate["id"])

    def initialize_run(self, run_id: str, document: dict, draft: list[dict], selection: list[str]) -> None:
        with self.lock, self.connection:
            self.connection.execute(
                "UPDATE agent_runs SET document=?,draft=?,selection=?,updated_at=? WHERE id=? AND document IS NULL",
                (self.encode(document), self.encode(draft), self.encode(selection), time.time(), run_id),
            )

    def save_response(self, run: dict, message: dict) -> None:
        with self.lock, self.connection:
            self.connection.execute(
                "UPDATE agent_runs SET pending_response=?,updated_at=? WHERE id=?",
                (self.encode(message), time.time(), run["id"]),
            )
            self.connection.execute(
                "INSERT INTO agent_messages(agent_id,run_id,task_id,payload,created_at) VALUES(?,?,?,?,?)",
                (run["agent_id"], run["id"], run["owner_task_id"], self.encode(message), time.time()),
            )

    def recent_messages(self, run_id: str, character_budget: int = 48_000) -> list[dict]:
        messages, size = [], 0
        with self.lock:
            rows = self.connection.execute(
                "SELECT payload FROM agent_messages WHERE run_id=? ORDER BY id DESC", (run_id,),
            )
            for row in rows:
                payload = json.loads(row["payload"])
                if payload.get("type") == "function_call_output":
                    message = {"role": "tool", "tool_call_id": payload["call_id"], "content": payload["output"]}
                else:
                    message = payload
                messages.append(message)
                size += len(row["payload"])
                if message["role"] == "assistant" and size >= character_budget:
                    break
        return list(reversed(messages))

    def begin_call(self, task_id: str, caller_id: str, call_id: str, name: str, arguments: str) -> dict | None:
        with self.lock, self.connection:
            row = self.connection.execute(
                "SELECT * FROM agent_tool_calls WHERE caller_id=? AND call_id=?", (caller_id, call_id),
            ).fetchone()
            if row:
                if row["name"] != name or row["arguments"] != arguments:
                    raise ValueError("A repeated call_id must have identical tool arguments")
                return json.loads(row["output"]) if row["output"] is not None else None
            self.connection.execute(
                "INSERT INTO agent_tool_calls VALUES(?,?,?,?,?,NULL,?)",
                (caller_id, call_id, task_id, name, arguments, time.time()),
            )
        return None

    def finish_call(self, caller_id: str, call_id: str, output: dict, run: dict | None = None) -> None:
        with self.lock, self.connection:
            cursor = self.connection.execute(
                "UPDATE agent_tool_calls SET output=? WHERE caller_id=? AND call_id=? AND output IS NULL",
                (self.encode(output), caller_id, call_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("Tool output already recorded or call_id not found")
            if run is not None:
                self.connection.execute(
                    "UPDATE agent_runs SET draft=?,selection=?,prepared=?,conversation=?,updated_at=? WHERE id=?",
                    (self.encode(run["draft"]), self.encode(run["selection"]), self.encode(run["prepared"]),
                     self.encode(run["conversation"]), time.time(), run["id"]),
                )
                self.connection.execute(
                    "INSERT INTO agent_messages(agent_id,run_id,task_id,payload,created_at) VALUES(?,?,?,?,?)",
                    (run["agent_id"], run["id"], run["owner_task_id"], self.encode({
                        "type": "function_call_output", "call_id": call_id, "output": self.encode(output),
                    }), time.time()),
                )

    def finish_response(self, run_id: str, conversation: list[dict]) -> None:
        with self.lock, self.connection:
            self.connection.execute(
                "UPDATE agent_runs SET pending_response=NULL,conversation=?,updated_at=? WHERE id=?",
                (self.encode(conversation), time.time(), run_id),
            )

    def complete_run(self, session_id: str, run_id: str, summary: str, error: str = "") -> dict:
        summary, error = summary[:1000], error[:1500]
        with self.lock, self.connection:
            run = self.get_run(session_id, run_id)
            agent = self._agent(session_id, run["agent_id"])
            if agent["state"] == "Shutdown":
                raise ValueError("A closed Agent cannot publish a new result")
            result_id = None
            if not error:
                result_id = stable_id("result", run_id)
                self._insert_result(result_id, session_id, run["owner_task_id"], run["prepared"], summary,
                                    run["agent_id"], run_id, run["document"].get("depends_on", []))
            status = "failed" if error else "completed"
            self.connection.execute("UPDATE agent_inputs SET status=? WHERE id=?", (status, run["input_id"]))
            self.connection.execute(
                "UPDATE agent_runs SET status=?,summary=?,error=?,result_id=?,pending_response=NULL,"
                "result_revision=?,updated_at=? WHERE id=?",
                (status, summary, error, result_id, agent["revision"] + 1, time.time(), run_id),
            )
            more = self.connection.execute(
                "SELECT id FROM agent_inputs WHERE agent_id=? AND status IN ('queued','running','paused') LIMIT 1",
                (run["agent_id"],),
            ).fetchone()
            self.connection.execute("UPDATE agent_sessions SET summary=? WHERE id=?", (summary or error, run["agent_id"]))
            self._touch(run["agent_id"], "Running" if more else "Final")
        return self.run_summary(self.get_run(session_id, run_id))

    @staticmethod
    def run_summary(run: dict) -> dict:
        return {"agent_id": run["agent_id"], "input_id": run["input_id"], "run_id": run["id"],
                "status": run["status"], "summary": run["summary"], "error": run["error"],
                "goal": run["message"][:240],
                "result_id": run["result_id"], "action_count": len(run["prepared"]),
                "revision": run["result_revision"]}

    def observed_results(self, session_id: str, agent_id: str, after: int = 0) -> list[dict]:
        with self.lock:
            self._agent(session_id, agent_id)
            rows = self.connection.execute(
                "SELECT r.id FROM agent_runs r JOIN agent_inputs i ON i.id=r.input_id "
                "WHERE i.agent_id=? AND r.result_revision>? ORDER BY i.rowid", (agent_id, after),
            ).fetchall()
            return [self.run_summary(self.get_run(session_id, row["id"])) for row in rows]

    def pause_agent(self, session_id: str, agent_id: str) -> dict:
        with self.lock, self.connection:
            agent = self._agent(session_id, agent_id)
            if agent["state"] != "Shutdown":
                self.connection.execute(
                    "UPDATE agent_runs SET status='paused',updated_at=? WHERE input_id IN "
                    "(SELECT id FROM agent_inputs WHERE agent_id=?) AND status='running'", (time.time(), agent_id),
                )
                self.connection.execute("UPDATE agent_inputs SET status='paused' WHERE agent_id=? AND status='running'", (agent_id,))
                self._touch(agent_id, "Shutdown")
        return self.get_agent(session_id, agent_id)

    def resume_agent(self, session_id: str, task_id: str, agent_id: str) -> dict:
        with self.lock, self.connection:
            self._check_task(session_id, task_id)
            agent = self._agent(session_id, agent_id)
            if agent["state"] == "Shutdown":
                pending = self.connection.execute(
                    "SELECT id FROM agent_inputs WHERE agent_id=? AND status IN ('paused','queued') LIMIT 1", (agent_id,),
                ).fetchone()
                self.connection.execute("UPDATE agent_sessions SET owner_task_id=? WHERE id=?", (task_id, agent_id))
                self._touch(agent_id, "Running" if pending else "Final")
        return self.get_agent(session_id, agent_id)

    def recover_interrupted(self) -> None:
        with self.lock, self.connection:
            self.connection.execute("UPDATE agent_runs SET status='paused' WHERE status='running'")
            self.connection.execute("UPDATE agent_inputs SET status='paused' WHERE status='running'")
            self.connection.execute(
                "UPDATE agent_sessions SET state='Shutdown',revision=revision+1,updated_at=? WHERE state='Running'", (time.time(),),
            )
            self.connection.execute(
                "UPDATE tasks SET status='cancelled',summary='服务已重新启动，任务已停止。子 Agent 进度已保存。',"
                "updated_at=? WHERE status IN ('queued','running')", (time.time(),),
            )

    def _insert_result(self, result_id: str, session_id: str, task_id: str, actions: list[dict], summary: str,
                       agent_id: str | None = None, run_id: str | None = None,
                       depends_on: list[str] | None = None) -> None:
        self.connection.execute(
            "INSERT OR IGNORE INTO drawing_results VALUES(?,?,?,?,?,?,?,?,0,'',?)",
            (result_id, session_id, task_id, agent_id, run_id, self.encode(actions),
             self.encode(depends_on or []), summary, time.time()),
        )

    def create_direct_result(self, session_id: str, task_id: str, call_id: str,
                             actions: list[dict], summary: str) -> dict:
        result_id = stable_id("result", f"{task_id}/{call_id}/direct")
        with self.lock, self.connection:
            self._check_task(session_id, task_id)
            self._insert_result(result_id, session_id, task_id, actions, summary)
        return self.get_result(session_id, result_id)

    def get_result(self, session_id: str, result_id: str) -> dict:
        with self.lock:
            row = self.connection.execute(
                "SELECT * FROM drawing_results WHERE id=? AND session_id=?", (result_id, session_id),
            ).fetchone()
        if row is None:
            raise KeyError("Drawing result does not belong to this canvas session")
        result = dict(row)
        result["actions"] = json.loads(result["actions"])
        result["depends_on"] = json.loads(result["depends_on"])
        return result

    @staticmethod
    def result_summary(result: dict) -> dict:
        actions = json.loads(result["actions"]) if isinstance(result["actions"], str) else result["actions"]
        dependencies = json.loads(result["depends_on"]) if isinstance(result["depends_on"], str) else result["depends_on"]
        names = [item["action"]["args"].get("name", item["action"]["kind"]) for item in actions]
        return {"result_id": result["id"], "agent_id": result["agent_id"], "run_id": result["run_id"],
                "summary": result["summary"], "action_count": len(actions),
                "committed_count": result["committed_count"], "depends_on": dependencies,
                "rejected_reason": result["rejected_reason"],
                "objects": names[:12]}

    def pending_results(self, session_id: str, agent_id: str | None = None, task_id: str | None = None) -> list[dict]:
        query = "SELECT id FROM drawing_results WHERE session_id=? AND rejected_reason='' AND committed_count<json_array_length(actions)"
        parameters = [session_id]
        if agent_id is not None:
            query += " AND agent_id=?"
            parameters.append(agent_id)
        if task_id is not None:
            query += " AND task_id=?"
            parameters.append(task_id)
        with self.lock:
            rows = self.connection.execute(query + " ORDER BY rowid", parameters).fetchall()
            results = [self.get_result(session_id, row["id"]) for row in rows]
        return [result for result in results if not result["rejected_reason"]
                and result["committed_count"] < len(result["actions"])]

    def reject_result(self, session_id: str, result_id: str, reason: str) -> dict:
        with self.lock, self.connection:
            self.get_result(session_id, result_id)
            self.connection.execute("UPDATE drawing_results SET rejected_reason=? WHERE id=?", (reason, result_id))
        return self.result_summary(self.get_result(session_id, result_id))

    def task_runs(self, session_id: str, task_id: str) -> list[dict]:
        with self.lock:
            self._check_task(session_id, task_id)
            rows = self.connection.execute("SELECT id FROM agent_runs WHERE owner_task_id=? ORDER BY rowid", (task_id,)).fetchall()
            return [self.run_summary(self.get_run(session_id, row["id"])) for row in rows]

    def task_results(self, session_id: str, task_id: str) -> list[dict]:
        with self.lock:
            self._check_task(session_id, task_id)
            rows = self.connection.execute(
                "SELECT id FROM drawing_results WHERE session_id=? AND task_id=? ORDER BY rowid", (session_id, task_id),
            ).fetchall()
            return [self.result_summary(self.get_result(session_id, row["id"])) for row in rows]

    def validate_commit(self, document_id: str, result_id: str, index: int) -> tuple[dict, bool]:
        row = self.connection.execute(
            "SELECT r.*,s.document_id FROM drawing_results r JOIN sessions s ON s.id=r.session_id WHERE r.id=?",
            (result_id,),
        ).fetchone()
        if row is None or row["document_id"] != document_id:
            raise KeyError("Drawing result does not belong to this document")
        actions = json.loads(row["actions"])
        if row["rejected_reason"]:
            raise ValueError("This drawing result has been rejected")
        if index < 0 or index >= len(actions):
            raise ValueError("Drawing action index is out of range")
        if row["committed_count"] > index:
            return actions[index], True
        if row["committed_count"] != index:
            raise ValueError("Drawing results must be committed in order")
        for dependency_id in json.loads(row["depends_on"]):
            dependency = self.get_result(row["session_id"], dependency_id)
            if dependency["committed_count"] != len(dependency["actions"]):
                raise ValueError(f"Apply dependency result first: {dependency_id}")
        return actions[index], False

    def advance_commit(self, result_id: str, index: int) -> None:
        cursor = self.connection.execute(
            "UPDATE drawing_results SET committed_count=committed_count+1 WHERE id=? AND committed_count=?",
            (result_id, index),
        )
        if cursor.rowcount != 1:
            raise ValueError("Drawing result progress changed during commit")
