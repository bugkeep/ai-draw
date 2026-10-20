import asyncio
import copy
import json
import time
from pathlib import Path

from studio.agents import DrawingAgents
from studio.runtime import DrawingRuntime
from studio.settings import ModelConfig, ModelSettings
from studio.store import StudioStore
from studio.vision import scene_digest


def test_saved_failed_correction_requests_natural_handoff_only_after_tool_errors_are_delivered(tmp_path, native_drawing_child):
    async def check():
        store = StudioStore(tmp_path / 'handoff.sqlite3')
        runtime = DrawingRuntime(store, ModelSettings(store))
        session = store.create_session()
        task = store.create_task(session['id'])
        child = native_drawing_child(store, runtime, session, task)
        reviews = runtime.children.draft_reviews
        try:
            child.call('create_element', {'type': 'ellipse'}, 'outline')
            # Replay recorded feedback for routing only; this is not an image-quality evaluation.
            fixture = json.loads((Path(__file__).parent / 'fixtures/draft-review-replan-response.json').read_text(encoding='utf-8'))
            result = {**fixture['result'], 'scene_hash': scene_digest(child.document), 'selection': child.run['selection']}

            def save_feedback(feedback):
                with store.lock, store.connection:
                    store.connection.execute('INSERT INTO draft_reviews VALUES(?,?,?,?,?,?,NULL) '
                        'ON CONFLICT(review_key) DO UPDATE SET result=excluded.result',
                        (result['review_key'], task['id'], child.run['id'], result['scene_hash'], json.dumps(feedback), time.time()))

            for revision in ('preparation', 'improved'):
                progress = copy.deepcopy(result)
                progress['assessment']['revision']['change'] = revision
                save_feedback(progress)
                assert reviews.replan_completion(child.run, progress) is None
                context = json.loads(reviews.context(progress, child.document)['content'])
                assert 'main_replan_request' not in context

            save_feedback(result)
            error = child.call('move_elements', {'ids': ['absent'], 'dx': 1, 'dy': 0}, 'invalid-target')
            assert error['ok'] is False
            assert reviews.replan_completion(child.run, result) is None
            messages = runtime.memory.prepare_messages(task['id'], child.agent['agent_id'], child.run['id'],
                'current-state', child.document, [])
            assert any(item.get('tool_call_id') == 'invalid-target' and json.loads(item['content']) == error
                for item in messages)
            runtime.memory.delivered(child.run['id'], messages)

            before = store.agents.get_run(session['id'], child.run['id'])
            handoff = reviews.replan_completion(child.run, result)
            context = json.loads(reviews.context(result, child.document, replan=handoff)['content'])
            assert context['current_private_draft_review'] == result
            assert context['main_replan_request'] == {key: handoff[key] for key in ('reason', 'review_key', 'next_strategy')}
            assert handoff['reason'] == 'ineffective_repair' and handoff['next_strategy']
            assert 'NO tool calls' in context['instruction']
            assert store.agents.get_run(session['id'], child.run['id']) == before
            assert before['status'] == 'running' and before['prepared']
            assert store.get_document(session['document']['id']) == session['document']
        finally:
            await runtime.close()
            store.close()
    asyncio.run(check())


def test_rebuilt_child_context_does_not_restore_a_discarded_private_summary(tmp_path, native_drawing_child):
    async def check():
        store = StudioStore(tmp_path / 'discarded-summary.sqlite3')
        runtime = DrawingRuntime(store, ModelSettings(store))
        session = store.create_session()
        task = store.create_task(session['id'])
        child = native_drawing_child(store, runtime, session, task)
        try:
            created = child.call('create_element', {'type': 'ellipse'}, 'discarded-outline')
            summary = 'Earlier private object: ' + created['changed_ids'][0]
            result = store.agents.complete_run(session['id'], child.run['id'], summary)
            store.agents.reject_result(session['id'], result['result_id'], '重新以共享画布为基础绘制')
            continued = native_drawing_child(store, runtime, session, task, child.agent)
            agent = store.agents.get_agent(session['id'], child.agent['agent_id'])
            model = DrawingAgents(store, ModelConfig(provider='ollama', model='llama3.1', api_base='http://localhost:11434'))
            context = json.loads(model.child_messages(continued.run, agent)[1]['content'])
            assert 'previous_work_summary' not in context
            assert context['previous_result']['result_id'] == result['result_id']
            assert context['previous_result']['committed_count'] == 0
            assert context['goal'] == continued.run['message']
            assert context['prepared_action_count'] == 0 and continued.run['draft'] == []
            assert store.agents.get_run(session['id'], child.run['id'])['summary'] == summary
        finally:
            await runtime.close()
            store.close()
    asyncio.run(check())


def test_main_context_identifies_private_targets_and_same_agent_continuation(tmp_path, native_drawing_child):
    async def check():
        store = StudioStore(tmp_path / 'private-targets.sqlite3')
        runtime = DrawingRuntime(store, ModelSettings(store))
        session = store.create_session()
        task = store.create_task(session['id'])
        child = native_drawing_child(store, runtime, session, task)
        try:
            created = child.call('create_element', {'type': 'ellipse'}, 'private-outline')
            fixture = json.loads((Path(__file__).parent / 'fixtures/draft-review-replan-response.json').read_text(encoding='utf-8'))
            # Bind a recorded finding to actual private native objects to test context ownership, not quality.
            completion = {'status': 'blocked', 'reason': 'ineffective_repair',
                'next_strategy': fixture['result']['assessment']['revision']['next_strategy'],
                'findings': [{**fixture['result']['assessment']['findings'][0], 'object_ids': created['changed_ids']}]}
            result = store.agents.complete_run(session['id'], child.run['id'], '未完成', completion=completion)
            context = runtime.result_reviews.repair_context(task['id'], result['result_id'])
            assert context['result_id'] == result['result_id']
            assert context['targets_present_only_in_pending_draft'] == created['changed_ids']
            assert context['continue_with'] == {'tool': 'send_input', 'agent_id': child.agent['agent_id'],
                                               'relevant_ids': created['changed_ids']}
            messages, deliveries = await runtime.result_reviews.context(task['id'])
            assert json.loads(messages[0]['content'])['private_repair_workspace'] == context
            assert deliveries
            continued = native_drawing_child(store, runtime, session, task, child.agent)
            assert continued.run['document']['depends_on'] == [result['result_id']]
            assert set(created['changed_ids']) <= {item['id'] for item in continued.run['draft'] if not item.get('isDeleted')}

            later = store.create_task(session['id'])
            assert runtime.result_reviews.repair_context(later['id'], result['result_id']) is None
            store.agents.reject_result(session['id'], result['result_id'], '放弃候选草稿')
            try:
                runtime.result_reviews.repair_context(task['id'], result['result_id'])
            except ValueError as error:
                assert 'rejected' in str(error)
            else:
                raise AssertionError('A discarded private workspace must not be offered for continuation')
            assert store.get_document(session['document']['id']) == session['document']
        finally:
            await runtime.close()
            store.close()
    asyncio.run(check())
