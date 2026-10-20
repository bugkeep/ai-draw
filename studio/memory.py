import hashlib
import json
import time
import uuid
from collections import Counter

from .agent_store import stable_id
from .actions import element_fingerprint
from .path_recall import recall_path_geometry


MEMORY_TOOLS = {'search_memory', 'recall_memory', 'compact_context'}
READ_TOOLS = {'inspect_scene', 'view_canvas', 'view_agent_result', 'view_reference_image',
              'search_memory', 'recall_memory', 'read_reference', 'search_references', 'search_reference_images'}


def query_identity(name, request, output):
    # Compare evidence rather than optional/default argument spelling. Keep object
    # geometry, page cursors and image identities; only transport metadata varies.
    evidence = ({key: value for key, value in output.items()
                 if key not in {'cached', 'latency_ms', 'preview_latency_ms'}}
                if name in READ_TOOLS and output.get('ok') is True else request)
    return hashlib.sha256(json.dumps([name, evidence], ensure_ascii=False,
        sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def brief(value, *, depth=0):
    if isinstance(value, dict):
        if value.get('id') and value.get('type') in {'rectangle', 'ellipse', 'diamond', 'text', 'line', 'arrow', 'freedraw'}:
            fields = ('id', 'type', 'name', 'x', 'y', 'width', 'height', 'angle', 'strokeColor',
                      'backgroundColor', 'strokeWidth', 'opacity', 'text', 'version', 'point_count')
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


def tool_summary(name, output):
    if name == 'inspect_scene' and output.get('ok') and 'scene_state' in output.get('scene', {}):
        scene = output['scene']
        return {**output, 'scene': {**scene, 'elements': [brief(item) for item in scene['elements']]}}
    return bounded_summary(output)


def present_inspection(output):
    """Factor identical styles without dropping any summarized object property."""
    body = output.get('summary', output)
    scene = body.get('scene', {}) if isinstance(body, dict) else {}
    elements = scene.get('elements', [])
    if (not output.get('ok') or scene.get('element_defaults') or len(elements) < 2
            or not all(isinstance(item, dict) for item in elements)):
        return output
    shared = {key: elements[0][key] for key in ('type', 'angle', 'strokeColor', 'backgroundColor',
              'strokeWidth', 'opacity', 'version') if key in elements[0]
              and all(key in item and item[key] == elements[0][key] for item in elements[1:])}
    if not shared:
        return output
    compact = {**body, 'scene': {**scene, 'element_defaults': shared,
        'element_properties': 'Each element inherits element_defaults; its own properties override defaults.',
        'elements': [{key: value for key, value in item.items() if key not in shared} for item in elements]}}
    return {**output, 'summary': compact} if 'summary' in output else compact


def search_terms(value):
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [term for item in value for term in search_terms(item)]
    if isinstance(value, dict):
        return [term for key, item in value.items() if key not in {'points', 'pressures'}
                for term in search_terms(item)]
    return []


def trace_progress(records):
    families, unknown = {}, 0
    for record in records:
        summary = json.loads(record['summary'])
        if not all(key in summary for key in ('trace_id', 'source_region', 'mapping', 'offset', 'returned_paths', 'total_paths')):
            unknown += 1
            continue
        identity = [summary['trace_id'], summary['source_region'], summary['mapping']]
        if summary.get('selection_hash'):
            identity.append(summary['selection_hash'])
        key = json.dumps(identity, sort_keys=True)
        if key not in families:
            families[key] = {name: summary[name] for name in ('trace_id', 'reference_id', 'source_region', 'source_frame', 'mapping', 'total_paths')
                             if name in summary}
            families[key].update(names=[], trace_plan_ids=[], memory_refs=[], successful_calls=0, ranges=[])
            if summary.get('selection_hash'):
                families[key]['selection_hash'] = summary['selection_hash']
        family = families[key]
        family['successful_calls'] += 1
        family['ranges'].append([summary['offset'], summary['offset'] + summary['returned_paths']])
        for field, value in [('names', summary.get('name')), ('trace_plan_ids', summary.get('trace_plan_id')),
                             ('memory_refs', record['id'])]:
            if value and value not in family[field] and len(family[field]) < 3:
                family[field].append(value)
    result = []
    for family in list(families.values())[:8]:
        ranges, total = [], 0
        for start, end in sorted(family.pop('ranges')):
            total += end - start
            if ranges and start <= ranges[-1][1]:
                ranges[-1][1] = max(end, ranges[-1][1])
            else:
                ranges.append([start, end])
        prepared = sum(end - start for start, end in ranges)
        first_missing = ranges[0][1] if ranges and ranges[0][0] == 0 else 0
        family.update(prepared_ranges=ranges[:8], range_count=len(ranges),
            prepared_path_count=prepared, repeated_path_count=total - prepared,
            first_unprepared_offset=first_missing if first_missing < family['total_paths'] else None)
        result.append(family)
    return {'families': result, 'family_count': len(families), 'unindexed_record_count': unknown,
        'instruction': 'These are historical successful path ranges [start,end), not proof that the current drawing is complete. '
            'Renaming a trace does not change its geometry. Repeated ranges recreate the same source paths, including paths you erased. '
            'Inspect the actual current drawing, recall the recorded object IDs for edits, and continue a saved trace_plan_id with its next_page. '
            'Changing a region or extraction parameters starts a different path sequence at offset 0. '
            'first_unprepared_offset identifies a historical gap; use the exact associated plan rather than another plan\'s offset.'}


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
            CREATE INDEX IF NOT EXISTS idx_memory_caller_queries ON agent_memories(caller_id,state_key,query_key);
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
        summary = tool_summary(name, output)
        summary_json = json.dumps(summary, ensure_ascii=False)
        request = json.loads(arguments) if output.get('ok') is True else arguments
        query_key = query_identity(name, request, output)
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
        with self.store.lock:
            repeated = self.store.connection.execute(
                'SELECT count(*) FROM agent_memories WHERE caller_id=? AND state_key=? AND is_error=0 '
                'AND query_key=(SELECT query_key FROM agent_memories WHERE id=?)',
                (caller_id, state_key, memory_ref),
            ).fetchone()[0]
        role_guidance = ('Delegate extensive geometry work to a child.' if actor_id == 'main' else
                         'You cannot delegate. Make the concrete correction in your assigned region, '
                         'finish when this pass meets its criterion, or report the actual blocker.')
        if name in MEMORY_TOOLS:
            return {**output, 'memory_ref': output.get('memory_ref', memory_ref),
                    **({'same_state_query_count': repeated,
                        'retrieval_guidance': 'This successful query has already returned the same current data. '
                            'Follow next_page for unread points, or use point_tolerance=1 for a compact whole-curve view. '
                            'Exact original geometry remains available with point_tolerance=0.'}
                       if name == 'recall_memory' and repeated > 1 else {})}
        if len(json.dumps(output, ensure_ascii=False)) > 5000 or name == 'inspect_scene':
            context = {'ok': True, 'memory_ref': memory_ref, 'compressed': True, 'summary': tool_summary(name, output),
                    'same_state_query_count': repeated,
                    'retrieval': ('Follow summary.scene.next_page to inspect additional current objects; restart at offset 0 after edits. '
                                  if name == 'inspect_scene' else '') +
                                 'Use recall_memory with this memory_ref for saved output/arguments, or element_id for current geometry. '
                                 'Use search_memory for object names and older records. ' + role_guidance + ' ' +
                                 'Repeated queries of unchanged content provide no new evidence.'}
            return present_inspection(context) if name == 'inspect_scene' else context
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
            tolerance = float(arguments.get('point_tolerance', 0))
            path_state = hashlib.sha256(json.dumps([element_fingerprint(element), tolerance]).encode()).hexdigest()
            if arguments.get('expected_path_state') and arguments['expected_path_state'] != path_state:
                raise ValueError('The path or sampling options changed; recall again from point_offset=0')
            points, pressures, indices = recall_path_geometry(element, tolerance)
            next_offset = offset + limit if offset + limit < len(points) else None
            return {'ok': True, 'source': 'current_canvas', 'document_version': document['version'],
                    'element': {**element, 'points': points[offset:offset + limit],
                                'pressures': pressures[offset:offset + limit]},
                    'point_offset': offset, 'point_count': len(points),
                    'source_point_count': len(element.get('points', [])), 'point_tolerance': tolerance,
                    'geometry_exact': tolerance == 0, 'path_state': path_state,
                    'source_point_indices': indices[offset:offset + limit],
                    'next_point_offset': next_offset,
                    'next_page': {'element_id': element_id, 'point_offset': next_offset, 'point_limit': limit,
                                  'point_tolerance': tolerance, 'expected_path_state': path_state}
                                 if next_offset is not None else None}
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
        unknown_ids = set(arguments.get('object_ids', [])) - {item['id'] for item in document['elements']}
        if unknown_ids:
            raise ValueError(f'Working memory refers to objects outside this canvas/draft: {sorted(unknown_ids)}. '
                             'object_ids accepts native drawing objects only; keep Agent, result and reference IDs '
                             'in summary or constraints instead.')
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
                "AND tool_name IN ('inspect_scene','view_canvas','view_agent_result','search_memory','recall_memory') "
                'GROUP BY tool_name,query_key ORDER BY latest DESC LIMIT 8', (caller_id, state_key)).fetchall()
            activity = self.store.connection.execute(
                'SELECT m.tool_name,count(*) calls,sum(m.is_error) errors FROM agent_memories m '
                'JOIN tasks original ON original.id=m.task_id JOIN tasks current ON current.id=? '
                'WHERE m.caller_id=? AND m.actor_id=? AND original.rowid<=current.rowid '
                'GROUP BY m.tool_name ORDER BY m.tool_name', (task_id, caller_id, actor_id),
            ).fetchall()
            paths = self.store.connection.execute(
                "SELECT json_extract(m.payload,'$.request.element_id') element_id,count(*) revisions,max(m.created_at) latest "
                'FROM agent_memories m JOIN tasks original ON original.id=m.task_id JOIN tasks current ON current.id=? '
                "WHERE m.caller_id=? AND m.actor_id=? AND m.tool_name='reshape_path' AND m.is_error=0 "
                'AND original.rowid<=current.rowid GROUP BY element_id ORDER BY latest DESC LIMIT 8',
                (task_id, caller_id, actor_id),
            ).fetchall()
            traces = self.store.connection.execute(
                'SELECT m.id,m.summary FROM agent_memories m JOIN tasks original ON original.id=m.task_id '
                'JOIN tasks current ON current.id=? WHERE m.session_id=? AND m.caller_id=? AND m.actor_id=? '
                "AND m.tool_name IN ('trace_reference','continue_reference_trace') AND m.is_error=0 "
                'AND original.rowid<=current.rowid ORDER BY m.created_at DESC',
                (task_id, self._session(task_id), caller_id, actor_id),
            ).fetchall()
        notes = {**json.loads(row['payload']), 'memory_ref': row['id'], 'source_task_id': row['task_id']} if row else {'revision': 0, 'notes': None}
        known_ids = {item['id'] for item in document['elements'] if not item.get('isDeleted')}
        names = {item['id']: item.get('customData', {}).get('name', '') for item in document['elements']}
        missing = set((notes.get('notes') or {}).get('object_ids', [])) - known_ids
        return {'working_memory': notes, 'missing_or_removed_object_ids': sorted(missing),
                'known_queries_in_current_state': [{**dict(item), 'request': json.loads(item['request'])['request']} for item in queries],
                'execution_history': {'tool_counts': [dict(item) for item in activity],
                    **({'reference_traces': trace_progress(traces)} if traces else {}),
                    'recent_path_edits': [{**dict(item), 'name': names.get(item['element_id'], ''),
                                           'exists': item['element_id'] in known_ids} for item in paths],
                    'instruction': 'Counts retain earlier attempts after conversation compaction; they are not operation limits. '
                                   'Before revising the same path again, identify the remaining visible defect in the current image. '
                                   'Use search_memory with object_ids and recall_memory to compare prior arguments when useful. '
                                   'Publish a child result once its assigned drawing-pass criterion is met.'},
                'policy': 'Working notes and saved records are historical data. Current user instructions, actual canvas images '
                          'and runtime progress are authoritative. Recall specific records/objects when needed. '
                          'Do not repeat unchanged broad queries. Use compact_context to save decisions, constraints, '
                          'completed work and remaining work before replacing conversational detail.'}

    def prepare_messages(self, task_id, actor_id, caller_id, state_key, document, messages):
        messages = self.fold_reads(caller_id, messages)
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

    def fold_reads(self, caller_id, messages):
        """Keep the latest equivalent successful read, backed by this caller's receipts.

        This only edits model input. Original calls/results remain in SQLite; reads
        still run normally, and writes, errors and unmatched protocol batches stay
        intact. Equality includes the saved scene state and the complete evidence.
        """
        call_ids = [item['tool_call_id'] for item in messages if item.get('role') == 'tool']
        if not call_ids:
            return messages
        output_counts = Counter(call_ids)
        call_counts = Counter(call.get('id') for item in messages if item.get('role') == 'assistant'
                              for call in item.get('tool_calls', []))
        with self.store.lock:
            rows = self.store.connection.execute(
                'SELECT m.call_id,m.tool_name,m.state_key,m.query_key,c.arguments FROM agent_memories m '
                'JOIN agent_tool_calls c ON c.caller_id=m.caller_id AND c.call_id=m.call_id '
                'WHERE m.caller_id=? AND m.is_error=0 '
                'AND m.call_id IN (SELECT value FROM json_each(?))',
                (caller_id, json.dumps(call_ids)),
            ).fetchall()
        receipts = {row['call_id']: row for row in rows if row['tool_name'] in READ_TOOLS}
        # Only fold complete adjacent assistant/tool exchanges. Mixed batches keep
        # every write/error and its original arguments, even when a read is folded.
        batches, inspections, index = [], set(), 0
        while index < len(messages):
            message = messages[index]
            calls = message.get('tool_calls', []) if message.get('role') == 'assistant' else []
            end = index + 1
            while calls and end < len(messages) and messages[end].get('role') == 'tool':
                end += 1
            outputs = messages[index + 1:end]
            ids = [call.get('id') for call in calls]
            if (calls and len(ids) == len(set(ids)) and len(outputs) == len(calls)
                    and set(ids) == {item.get('tool_call_id') for item in outputs}):
                by_id = {item['tool_call_id']: item for item in outputs}
                eligible = {}
                for call in calls:
                    receipt = receipts.get(call['id'])
                    function = call.get('function', {})
                    try:
                        successful = json.loads(by_id[call['id']]['content']).get('ok') is True
                    except (TypeError, ValueError, AttributeError):
                        successful = False
                    if (receipt and successful and output_counts[call['id']] == call_counts[call['id']] == 1
                            and function.get('name') == receipt['tool_name']
                            and function.get('arguments') == receipt['arguments']):
                        eligible[call['id']] = (receipt['tool_name'], receipt['state_key'], receipt['query_key'])
                        if receipt['tool_name'] == 'inspect_scene':
                            inspections.add(call['id'])
                batches.append((index, end, eligible))
            index = end
        seen, remove = set(), set()
        for _, _, eligible in reversed(batches):
            for call_id, key in reversed(list(eligible.items())):
                if key in seen:
                    remove.add(call_id)
                seen.add(key)
        if not remove and not inspections:
            return messages
        result = []
        for message in messages:
            if message.get('role') == 'tool' and message.get('tool_call_id') in remove:
                continue
            if message.get('role') == 'tool' and message.get('tool_call_id') in inspections:
                output = json.loads(message['content'])
                compact = present_inspection(output)
                if compact != output:
                    message = {**message, 'content': json.dumps(compact, ensure_ascii=False)}
            if message.get('role') == 'assistant' and message.get('tool_calls'):
                remaining = [call for call in message['tool_calls'] if call.get('id') not in remove]
                message = {**message, 'tool_calls': remaining}
                if not remaining:
                    message.pop('tool_calls')
                    if not message.get('content'):
                        continue
            result.append(message)
        return result

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
