import json

from .child_drawing_memory import DRAWING_WRITES
from .vision import scene_digest


def checkpoint_reason(reason):
    if reason and set(reason) <= {'first_changed_draft', 'changed_after_review'}:
        return None
    return reason


def current_feedback(store, run):
    with store.lock:
        row = store.connection.execute(
            'SELECT d.result,d.scene_hash,d.observed_at FROM draft_reviews d '
            'JOIN agent_runs r ON r.id=d.run_id JOIN agent_inputs i ON i.id=r.input_id '
            'WHERE d.run_id=? AND d.task_id=? AND i.agent_id=? AND i.id=? '
            'ORDER BY d.created_at DESC LIMIT 1',
            (run['id'], run['owner_task_id'], run['agent_id'], run['input_id']),
        ).fetchone()
    if row is None:
        return None
    document = {**run['document'], 'elements': run['draft'], 'selection': run['selection']}
    feedback = json.loads(row['result'])
    if (row['scene_hash'] != scene_digest(document)
            or feedback.get('selection', run['selection']) != run['selection']
            or feedback.get('status') == 'error' and row['observed_at'] is not None):
        return None
    return {**feedback, 'cached': True}


def verification_checkpoint(store, run, reason):
    if not reason or set(reason) != {'changed_after_review'}:
        return None
    with store.lock:
        review = store.connection.execute('SELECT created_at,result,observed_at FROM draft_reviews '
            'WHERE review_key=? AND run_id=? AND task_id=?',
            (reason['changed_after_review'], run['id'], run['owner_task_id'])).fetchone()
        last = store.connection.execute('SELECT call_id,name,output FROM agent_tool_calls '
            'WHERE caller_id=? AND task_id=? ORDER BY rowid DESC LIMIT 1',
            (run['id'], run['owner_task_id'])).fetchone()
        changed = store.connection.execute('SELECT name,created_at FROM agent_tool_calls '
            "WHERE caller_id=? AND task_id=? AND json_extract(output,'$.ok')=1 "
            "AND json_extract(output,'$.action_count')>0 AND name IN (SELECT value FROM json_each(?)) "
            'ORDER BY rowid DESC LIMIT 1',
            (run['id'], run['owner_task_id'], json.dumps(DRAWING_WRITES))).fetchone()
    if (review is None or review['observed_at'] is None or json.loads(review['result'])['status'] != 'repair'
            or last is None or last['name'] != 'view_canvas' or not last['output']
            or json.loads(last['output']).get('ok') is not True
            or changed is None or changed['created_at'] <= review['created_at']
            or changed['name'] in {'erase_strokes', 'delete_elements'}):
        return None
    return {'repair_verification': {'review_key': reason['changed_after_review'], 'call_id': last['call_id']}}


def execution_context(run, messages):
    prompt = json.loads(messages[1]['content'])
    previous = prompt.get('previous_drawspec_report')
    if previous:
        live = {item['id'] for item in run['draft'] if not item.get('isDeleted')}
        prompt['inherited_form_bindings'] = {
            'composition_id': previous['composition_id'],
            'bindings': [{'form_id': binding['form_id'],
                'object_ids': [key for key in binding['object_ids'] if key in live]}
                for binding in previous['bindings'] if set(binding['object_ids']) & live],
            'unresolved': previous['unresolved'],
            'instruction': 'Historical form associations filtered to IDs still present in THIS draft. '
                'Verify the current images; these associations do not prove current visibility or correctness. '
                'Replace a binding after rebuilding its form. The full previous report remains in SQLite.',
        }
        del prompt['previous_drawspec_report']
    return [messages[0], {**messages[1], 'content': json.dumps(prompt, ensure_ascii=False)}, *messages[2:]]


def continue_repair(completion):
    return completion['completion'].get('review_status') == 'repair'
