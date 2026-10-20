"""Native tool history and delivery receipts; no model replies or artwork grading."""

import asyncio
import json
import time

from studio.runtime import DrawingRuntime
from studio.settings import ModelSettings
from studio.store import StudioStore


def test_cached_review_keeps_repair_evidence_until_a_new_feedback_receipt(tmp_path, native_drawing_child):
    async def check():
        store = StudioStore(tmp_path / 'review-history.sqlite3')
        runtime = DrawingRuntime(store, ModelSettings(store))
        session = store.create_session()
        task = store.create_task(session['id'])
        child = native_drawing_child(store, runtime, session, task)
        try:
            def exchange(name, args, call_id):
                result = child.call(name, args, call_id)
                return [{'role': 'assistant', 'content': 'Use these object facts for the next repair.',
                         'tool_calls': [{'id': call_id, 'type': 'function', 'function': {
                             'name': name, 'arguments': json.dumps(args)}}]},
                        {'role': 'tool', 'tool_call_id': call_id, 'content': json.dumps(result)}]

            def receipt(key):
                review = {'review_key': key, 'status': 'repair'}
                # Only delivery metadata is needed to exercise scheduling, not an LLM assessment.
                with store.connection:
                    store.connection.execute('INSERT INTO draft_reviews VALUES(?,?,?,?,?,?,NULL)',
                        (key, task['id'], child.run['id'], 'delivery-receipt', json.dumps(review), time.time()))
                return review

            write = exchange('create_element', {'type': 'ellipse', 'name': 'repair target'}, 'target')
            old_read = exchange('inspect_scene', {'limit': 1}, 'previous-review-read')
            runtime.memory.delivered(child.run['id'], old_read)
            error = exchange('move_elements', {'ids': ['missing'], 'dx': 1, 'dy': 0}, 'unread-error')
            review = receipt('first-feedback')
            first = runtime.children._review_history(child.run, review, [*write, *old_read, *error])
            assert old_read[0] not in first and all(item in first for item in error)
            runtime.children.draft_reviews.delivered(child.run['id'], review['review_key'])
            target_id = child.run['draft'][0]['id']
            current_read = exchange('inspect_scene', {'ids': [target_id]}, 'current-object-facts')
            runtime.memory.delivered(child.run['id'], current_read)
            follow_up = exchange('search_memory', {'query': 'repair target'}, 'new-related-records')
            history = [*first, *current_read, *follow_up]
            cached = runtime.children._review_history(child.run, {**review, 'cached': True}, history)
            assert cached == history
            # The stored receipt governs this decision, rather than the response's cached flag.
            assert runtime.children._review_history(child.run, {**review, 'cached': False}, history) == history
            messages = runtime.memory.prepare_messages(task['id'], child.agent['agent_id'], child.run['id'],
                f"{child.run['id']}:{len(child.run['prepared'])}", child.document, cached)
            retained = {item['tool_call_id'] for item in messages if item.get('role') == 'tool'}
            assert {'current-object-facts', 'new-related-records', 'unread-error'} <= retained
            fresh = receipt('changed-draft-feedback')
            checkpoint = runtime.children._review_history(child.run, {**fresh, 'cached': True}, history)
            assert current_read[0] not in checkpoint
            assert all(item in checkpoint for item in [*error, *follow_up])
            assert runtime.children._review_history(child.run, {'status': 'error'}, history) == history
            assert runtime.children._review_history(child.run, None, history) == history
            assert len(child.run['prepared']) == 1 and store.get_document(session['document']['id'])['elements'] == []
            assert store.connection.execute('SELECT count(*) FROM agent_tool_calls WHERE caller_id=?',
                                            (child.run['id'],)).fetchone()[0] == 5
        finally:
            await runtime.close()
            store.close()
    asyncio.run(check())
