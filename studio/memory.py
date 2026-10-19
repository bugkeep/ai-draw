import hashlib
import json
import time
import uuid

from .agent_store import stable_id


MEMORY_TOOLS = {'search_memory', 'recall_memory', 'compact_context'}


def brief(value, *, depth=0):
    if isinstance(value, dict):
        if value.get('id') and value.get('type') in {'rectangle', 'ellipse', 'diamond', 'text', 'line', 'arrow', 'freedraw'}:
            fields = ('id', 'type', 'name', 'x', 'y', 'width', 'height', 'angle', 'strokeColor',
                      'backgroundColor', 'strokeWidth', 'opacity', 'text', 'version')
            result = {key: value[key] for key in fields if key in value}
            result['name'] = value.get('name') or value.get('customData', {}).get('name', '')
            if 'points' in value:
                result['point_count'] = len(value['points'])
            return result
        result = {key: brief(item, depth=depth + 1) for key, item in value.items()
                  if key not in {'points', 'pressures'}}
        if 'points' in value:
            result['point_count'] = len(value['points'])
        return result
    if isinstance(value, list):
        items = [brief(item, depth=depth + 1) for item in value[:12]]
        return items if len(value) <= 12 else {'items': items, 'total_count': len(value)}
    if isinstance(value, str) and len(value) > 600:
        return value[:600] + ' [continued in memory]'
    return value


def bounded_summary(value):
    summary = brief(value)
    encoded = json.dumps(summary, ensure_ascii=False)
    if len(encoded) > 6000:
        return {'preview': encoded[:4000], 'more_in_memory': True}
    return summary


def search_terms(value):
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [term for item in value for term in search_terms(item)]
    if isinstance(value, dict):
        return [term for key, item in value.items() if key not in {'points', 'pressures'}
                for term in search_terms(item)]
    return []


class AgentMemory:
    def __init__(self, store):
        self.store = store
        store.connection.executescript('''
            CREATE TABLE IF NOT EXISTS agent_memories (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                actor_id TEXT NOT NULL, kind TEXT NOT NULL,
                caller_id TEXT, call_id TEXT, tool_name TEXT NOT NULL,
                state_key TEXT NOT NULL, query_key TEXT NOT NULL,
                summary TEXT NOT NULL, payload TEXT NOT NULL,
                search_text TEXT NOT NULL, is_error INTEGER NOT NULL,
                created_at REAL NOT NULL, observed_at REAL,
                UNIQUE(caller_id,call_id),
                FOREIGN KEY(caller_id,call_id) REFERENCES agent_tool_calls(caller_id,call_id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_memory_scope ON agent_memories(session_id,actor_id,created_at);
            CREATE INDEX IF NOT EXISTS idx_memory_queries ON agent_memories(session_id,actor_id,state_key,query_key);
            CREATE INDEX IF NOT EXISTS idx_memory_unread ON agent_memories(caller_id,is_error,observed_at);
            CREATE TABLE IF NOT EXISTS agent_working_memory (
                session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                actor_id TEXT NOT NULL, revision INTEGER NOT NULL,
                memory_ref TEXT NOT NULL REFERENCES agent_memories(id) ON DELETE CASCADE,
                PRIMARY KEY(session_id,actor_id)
            );
        ''')
        store.connection.commit()

    def _session(self, task_id):
        return self.store.get_task(task_id)['session_id']

    def _record(self, task_id, actor_id, memory_ref):
        session_id = self._session(task_id)
        with self.store.lock:
            row = self.store.connection.execute(
                'SELECT m.* FROM agent_memories m JOIN tasks original ON original.id=m.task_id '
                'JOIN tasks current ON current.id=? WHERE m.id=? AND m.session_id=? AND m.actor_id=? '
                'AND original.rowid<=current.rowid', (task_id, memory_ref, session_id, actor_id),
            ).fetchone()
        if row is None:
            raise KeyError(f'Memory {memory_ref} does not belong to this Agent and canvas, or comes from a later task')
        return dict(row)

    def register(self, task_id, actor_id, caller_id, call_id, name, arguments, output, state_key):
        memory_ref = stable_id('memory', f'{caller_id}/{call_id}')
        summary = bounded_summary(output)
        summary_json = json.dumps(summary, ensure_ascii=False)
        request = json.loads(arguments) if output.get('ok') is True else arguments
        query_key = hashlib.sha256(json.dumps([name, request], sort_keys=True).encode()).hexdigest()
        with self.store.lock, self.store.connection:
            self.store.connection.execute('INSERT OR IGNORE INTO agent_memories VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)',
                (memory_ref, self._session(task_id), task_id, actor_id, 'tool', caller_id, call_id, name,
                 state_key, query_key, summary_json, json.dumps({'request': brief(request)}, ensure_ascii=False),
                 ' '.join([name, *search_terms(request), *search_terms(output)]).casefold(),
                 int(output.get('ok') is False), time.time()))
        return memory_ref

    def for_context(self, task_id, actor_id, caller_id, call_id, name, arguments, output, state_key):
        memory_ref = self.register(task_id, actor_id, caller_id, call_id, name, arguments, output, state_key)
        if output.get('ok') is False:
            return {**output, 'memory_ref': memory_ref}
        if name in MEMORY_TOOLS:
            return {**output, 'memory_ref': output.get('memory_ref', memory_ref)}
        with self.store.lock:
            repeated = self.store.connection.execute(
                'SELECT count(*) FROM agent_memories WHERE session_id=? AND actor_id=? AND state_key=? '
                'AND query_key=(SELECT query_key FROM agent_memories WHERE id=?)',
                (self._session(task_id), actor_id, state_key, memory_ref),
            ).fetchone()[0]
        if len(json.dumps(output, ensure_ascii=False)) > 5000 or name == 'inspect_scene':
            return {'ok': True, 'memory_ref': memory_ref, 'compressed': True, 'summary': bounded_summary(output),
                    'same_state_query_count': repeated,
                    'retrieval': 'Use recall_memory with this memory_ref for saved output/arguments, or element_id for current geometry. '
                                 'Use search_memory for object names and older records. Delegate extensive geometry work to a child. '
                                 'Repeated queries of unchanged content provide no new evidence.'}
        return {**output, 'memory_ref': memory_ref, **({'same_state_query_count': repeated} if repeated > 1 else {})}

    def _index_older(self, task_id, actor_id):
        session_id = self._session(task_id)
        with self.store.lock:
            rows = self.store.connection.execute(
                'SELECT c.* FROM agent_tool_calls c JOIN tasks t ON t.id=c.task_id JOIN tasks current ON current.id=? '
                'LEFT JOIN agent_runs r ON r.id=c.caller_id LEFT JOIN agent_inputs i ON i.id=r.input_id '
                'LEFT JOIN agent_memories m ON m.caller_id=c.caller_id AND m.call_id=c.call_id '
                'WHERE t.session_id=? AND t.rowid<=current.rowid AND c.output IS NOT NULL AND m.id IS NULL '
                "AND ((?='main' AND c.caller_id='main:'||c.task_id) OR i.agent_id=?) ORDER BY c.created_at",
                (task_id, session_id, actor_id, actor_id),
            ).fetchall()
        for row in rows:
            self.register(row['task_id'], actor_id, row['caller_id'], row['call_id'], row['name'],
                          row['arguments'], json.loads(row['output']), 'historical')
        if actor_id != 'main':
            return
        with self.store.lock, self.store.connection:
            messages = self.store.connection.execute(
                'SELECT m.* FROM messages m JOIN tasks t ON t.id=m.task_id JOIN tasks current ON current.id=? '
                'WHERE m.session_id=? AND t.rowid<=current.rowid ORDER BY m.created_at', (task_id, session_id),
            ).fetchall()
            for message in messages:
                payload = {'role': message['role'], 'content': message['content'], 'message_id': message['id']}
                summary = {'role': message['role'], 'excerpt': message['content'][:400]}
                self.store.connection.execute('INSERT OR IGNORE INTO agent_memories VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)',
                    (stable_id('memory', f"message/{message['id']}"), session_id, message['task_id'], actor_id,
                     'message', None, None, '', '', '', json.dumps(summary, ensure_ascii=False),
                     json.dumps(payload, ensure_ascii=False), message['content'].casefold(), 0, message['created_at']))

    def search(self, task_id, actor_id, arguments, document):
        self._index_older(task_id, actor_id)
        query = arguments.get('query', '').casefold().strip()
        words = query.split()
        object_ids = set(arguments.get('object_ids', []))
        offset, limit = arguments.get('offset', 0), arguments.get('limit', 8)
        matches = [element for element in document['elements'] if not element.get('isDeleted') and
                   (element['id'] in object_ids or not object_ids and (not words or any(word in
                    f"{element['id']} {element.get('customData', {}).get('name', '')} {element.get('text', '')}".casefold()
                    for word in words)))]
        with self.store.lock:
            clauses = ' OR '.join('instr(m.search_text,?)>0' for _ in words) or '1'
            id_clause = ' OR '.join('instr(m.search_text,?)>0' for _ in object_ids) or '1'
            rows = self.store.connection.execute(
                'SELECT m.* FROM agent_memories m JOIN tasks original ON original.id=m.task_id '
                'JOIN tasks current ON current.id=? WHERE m.session_id=? AND m.actor_id=? '
                f'AND original.rowid<=current.rowid AND ({clauses}) AND ({id_clause}) ORDER BY m.created_at DESC LIMIT ? OFFSET ?',
                (task_id, self._session(task_id), actor_id, *words, *object_ids, limit + 1, offset),
            ).fetchall()
        return {'ok': True, 'objects': [brief(item) for item in matches[offset:offset + limit]],
                'object_count': len(matches), 'records': [{'memory_ref': row['id'], 'kind': row['kind'],
                    'tool': row['tool_name'], 'task_id': row['task_id'], 'excerpt': row['summary'][:400]}
                    for row in rows[:limit]],
                'next_offset': offset + limit if len(rows) > limit or len(matches) > offset + limit else None,
                'instruction': 'Object entries describe the current canvas/draft. Saved records are historical; '
                               'verify current geometry before editing. Pages do not limit total work.'}

    def recall(self, task_id, actor_id, arguments, document):
        memory_ref = arguments.get('memory_ref')
        row = self._record(task_id, actor_id, memory_ref) if memory_ref else None
        element_id = arguments.get('element_id')
        if element_id:
            element = next((item for item in document['elements'] if item['id'] == element_id and not item.get('isDeleted')), None)
            if element is None:
                raise KeyError('Requested object does not exist in this canvas/draft')
            offset, limit = arguments.get('point_offset', 0), arguments.get('point_limit', 128)
            points = element.get('points', [])
            return {'ok': True, 'source': 'current_canvas', 'document_version': document['version'],
                    'element': {**element, 'points': points[offset:offset + limit],
                                'pressures': element.get('pressures', [])[offset:offset + limit]},
                    'point_offset': offset, 'point_count': len(points),
                    'next_point_offset': offset + limit if offset + limit < len(points) else None}
        if row is None:
            raise ValueError('Provide a memory_ref or an element_id')
        section = arguments.get('section', 'summary')
        if section == 'summary':
            return {'ok': True, 'memory_ref': memory_ref, 'kind': row['kind'],
                    'summary': json.loads(row['summary']), 'source_task_id': row['task_id']}
        if row['kind'] == 'tool':
            with self.store.lock:
                record = self.store.connection.execute('SELECT arguments,output FROM agent_tool_calls WHERE caller_id=? AND call_id=?',
                                                       (row['caller_id'], row['call_id'])).fetchone()
            content = record[section]
        else:
            if section == 'arguments':
                raise ValueError('This memory is a note/message, not a function call with arguments')
            content = row['payload']
        offset, length = arguments.get('offset', 0), arguments.get('max_chars', 4000)
        return {'ok': True, 'memory_ref': memory_ref, 'historical': True, 'source_task_id': row['task_id'],
                'section': section, 'format': 'JSON text', 'text': content[offset:offset + length],
                'total_characters': len(content), 'next_offset': offset + length if offset + length < len(content) else None}

    def compact(self, task_id, actor_id, arguments, document):
        session_id = self._session(task_id)
        if set(arguments.get('object_ids', [])) - {item['id'] for item in document['elements']}:
            raise ValueError('Working memory refers to objects outside this canvas/draft')
        for memory_ref in arguments.get('evidence_refs', []):
            self._record(task_id, actor_id, memory_ref)
        with self.store.lock, self.store.connection:
            prior = self.store.connection.execute('SELECT revision FROM agent_working_memory WHERE session_id=? AND actor_id=?',
                                                 (session_id, actor_id)).fetchone()
            revision = prior['revision'] if prior else 0
            if revision != arguments.get('expected_revision', 0):
                raise ValueError(f'Working memory changed: use current revision {revision}')
            memory_ref = 'memory_' + uuid.uuid4().hex
            notes = {key: value for key, value in arguments.items() if key != 'expected_revision'}
            encoded = json.dumps(notes, ensure_ascii=False)
            payload = json.dumps({'revision': revision + 1, 'notes': notes}, ensure_ascii=False)
            self.store.connection.execute('INSERT INTO agent_memories VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)',
                (memory_ref, session_id, task_id, actor_id, 'working', None, None, '', '', '', encoded,
                 payload, encoded.casefold(), 0, time.time()))
            self.store.connection.execute('INSERT INTO agent_working_memory VALUES(?,?,?,?) '
                'ON CONFLICT(session_id,actor_id) DO UPDATE SET revision=excluded.revision,memory_ref=excluded.memory_ref',
                (session_id, actor_id, revision + 1, memory_ref))
        return {'ok': True, 'compacted': True, 'memory_ref': memory_ref, 'revision': revision + 1,
                'instruction': 'Working notes are saved. Continue from the current canvas/draft; prior tool records remain searchable.'}

    def snapshot(self, task_id, actor_id, caller_id, state_key, document):
        with self.store.lock:
            row = self.store.connection.execute('SELECT m.id,m.task_id,m.payload FROM agent_memories m '
                'JOIN tasks original ON original.id=m.task_id JOIN tasks current ON current.id=? '
                "WHERE m.session_id=? AND m.actor_id=? AND m.kind='working' AND original.rowid<=current.rowid "
                'ORDER BY m.created_at DESC LIMIT 1', (task_id, self._session(task_id), actor_id)).fetchone()
            queries = self.store.connection.execute('SELECT tool_name,query_key,count(*) repeat_count,max(created_at) latest,'
                'min(id) memory_ref,min(payload) request FROM agent_memories WHERE caller_id=? AND state_key=? '
                "AND tool_name IN ('inspect_scene','view_canvas','search_memory','recall_memory') "
                'GROUP BY tool_name,query_key ORDER BY latest DESC LIMIT 8', (caller_id, state_key)).fetchall()
        notes = {**json.loads(row['payload']), 'memory_ref': row['id'], 'source_task_id': row['task_id']} if row else {'revision': 0, 'notes': None}
        known_ids = {item['id'] for item in document['elements'] if not item.get('isDeleted')}
        missing = set((notes.get('notes') or {}).get('object_ids', [])) - known_ids
        return {'working_memory': notes, 'missing_or_removed_object_ids': sorted(missing),
                'known_queries_in_current_state': [{**dict(item), 'request': json.loads(item['request'])['request']} for item in queries],
                'policy': 'Working notes and saved records are historical data. Current user instructions, actual canvas images '
                          'and runtime progress are authoritative. Recall specific records/objects when needed. '
                          'Do not repeat unchanged broad queries. Use compact_context to save decisions, constraints, '
                          'completed work and remaining work before replacing conversational detail.'}

    def prepare_messages(self, task_id, actor_id, caller_id, state_key, document, messages):
        present = {message['tool_call_id'] for message in messages if message.get('role') == 'tool'}
        with self.store.lock:
            unread = self.store.connection.execute('SELECT m.id,c.* FROM agent_memories m JOIN agent_tool_calls c '
                'ON c.caller_id=m.caller_id AND c.call_id=m.call_id WHERE m.caller_id=? '
                'AND m.is_error=1 AND m.observed_at IS NULL ORDER BY m.created_at', (caller_id,)).fetchall()
        extra = []
        for row in unread:
            if row['call_id'] not in present:
                extra.extend([{'role': 'assistant', 'tool_calls': [{'id': row['call_id'], 'type': 'function',
                    'function': {'name': row['name'], 'arguments': row['arguments']}}]},
                    {'role': 'tool', 'tool_call_id': row['call_id'], 'content': row['output']}])
        return [*messages, {'role': 'user', 'content': json.dumps(
            self.snapshot(task_id, actor_id, caller_id, state_key, document), ensure_ascii=False)}, *extra]

    def delivered(self, caller_id, messages):
        call_ids = {message['tool_call_id'] for message in messages if message.get('role') == 'tool'}
        with self.store.lock, self.store.connection:
            for call_id in call_ids:
                self.store.connection.execute('UPDATE agent_memories SET observed_at=? WHERE caller_id=? AND call_id=?',
                                             (time.time(), caller_id, call_id))

    def call(self, task_id, actor_id, name, arguments, document):
        if name == 'search_memory':
            return self.search(task_id, actor_id, arguments, document)
        if name == 'recall_memory':
            return self.recall(task_id, actor_id, arguments, document)
        if name == 'compact_context':
            return self.compact(task_id, actor_id, arguments, document)
        raise ValueError('Unknown memory tool')
