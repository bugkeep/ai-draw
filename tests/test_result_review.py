import asyncio
import io
import json
from contextlib import asynccontextmanager

import pytest
from PIL import Image

from studio.actions import element_fingerprint
from studio.agent_tools import validate_tool
from studio.agents import DrawingAgents, prepare_drawing_tool
from studio.runtime import DrawingRuntime
from studio.settings import ModelConfig, ModelSettings
from studio.store import StudioStore, VersionConflict


@asynccontextmanager
async def workspace(tmp_path):
    store = StudioStore(tmp_path / 'result-review.sqlite3')
    runtime = DrawingRuntime(store, ModelSettings(store))
    session = store.create_session('提交前预览')
    task = store.create_task(session['id'])
    try:
        yield store, runtime, session, task
    finally:
        await runtime.close()
        store.close()


def publish_result(store, runtime, session, task, calls, name='child'):
    records = store.agents
    agent = records.create_agent(session['id'], task['id'], name, '准备指定图形修改', name, [])
    run = runtime.children._initialize(session['id'], records.next_run(session['id'], agent['agent_id'], task['id']))
    for index, (tool, arguments) in enumerate(calls):
        call_id = f'{name}-{index}'
        assert records.begin_call(task['id'], run['id'], call_id, tool, json.dumps(arguments)) is None
        draft, selection, actions, output = prepare_drawing_tool(tool, arguments, run['document'], run['draft'], run['selection'])
        run.update(draft=draft, selection=selection, prepared=[*run['prepared'], *actions])
        records.finish_call(run['id'], call_id, output, run)
    return records.complete_run(session['id'], run['id'], '指定修改已准备')['result_id']


async def main_tool(runtime, store, session, task, name, arguments, call_id):
    models = DrawingAgents(store, ModelConfig(provider='ollama', model='llama3.1', api_base='http://localhost:11434'))
    return await runtime.call_main_tool(models, task['id'], session['id'], session['document']['id'], {
        'id': call_id, 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(arguments)}})


def test_preview_replays_on_current_canvas_without_mutation_and_matches_committed_pixels(tmp_path):
    async def check():
        async with workspace(tmp_path) as (store, runtime, session, task):
            document_id = session['document']['id']
            result_id = publish_result(store, runtime, session, task, [('create_element', {
                'type': 'rectangle', 'name': '候选主体', 'x': 10, 'y': 20, 'width': 80, 'height': 60, 'roughness': 1})])
            before = store.commit_action(document_id, {'kind': 'create_element', 'args': {
                'type': 'ellipse', 'name': '其他窗口新增', 'x': 200, 'y': 100, 'width': 40, 'height': 40}}, {})['document']
            history = store.get_history(document_id, 'undo')
            with pytest.raises(ValueError, match='Inspect this proposed child result'):
                runtime.result_reviews.require_review(task['id'], result_id)
            messages, deliveries = await runtime.result_reviews.context(task['id'])
            assert messages and deliveries
            assert store.get_document(document_id) == before and store.get_history(document_id, 'undo') == history
            assert store.agents.get_result(session['id'], result_id)['committed_count'] == 0
            with pytest.raises(ValueError):
                runtime.result_reviews.require_review(task['id'], result_id)
            runtime.result_reviews.delivered(task['id'], deliveries)
            observation_id = next(iter(deliveries.values()))[0]
            png = runtime.vision.image(session['id'], observation_id)
            output = await main_tool(runtime, store, session, task, 'apply_agent_result', {'result_id': result_id}, 'approve')
            assert output['ok'] and store.agents.get_result(session['id'], result_id)['committed_count'] == 1
            document = store.get_document(document_id)
            assert len(document['elements']) == 2
            assert element_fingerprint(document['elements'][0]) == element_fingerprint(before['elements'][0])
            rendered = await runtime.vision.renderer.render(document)
            with Image.open(io.BytesIO(png)) as proposed, Image.open(io.BytesIO(rendered['png'])) as actual:
                assert proposed.size == actual.size and proposed.tobytes() == actual.tobytes()
            duplicate = await main_tool(runtime, store, session, task, 'apply_agent_result', {'result_id': result_id}, 'approve-again')
            assert duplicate['ok'] and store.get_document(document_id) == document
    asyncio.run(check())


def test_unseen_or_stale_result_cannot_be_applied_by_the_main_agent(tmp_path):
    async def check():
        async with workspace(tmp_path) as (store, runtime, session, task):
            result_id = publish_result(store, runtime, session, task, [('create_element', {'type': 'ellipse', 'name': '头部'})])
            before = store.get_document(session['document']['id'])
            rejected = await main_tool(runtime, store, session, task, 'apply_agent_result', {'result_id': result_id}, 'unseen')
            assert rejected['ok'] is False and 'proposed child result' in rejected['error']
            assert store.get_document(before['id']) == before
            _, deliveries = await runtime.result_reviews.context(task['id'])
            runtime.result_reviews.delivered(task['id'], deliveries)
            changed = store.commit_action(before['id'], {'kind': 'create_element', 'args': {
                'type': 'text', 'text': '手工注释', 'x': 200, 'y': 20}}, {})['document']
            with pytest.raises(ValueError, match='fresh preview|Inspect this proposed'):
                runtime.result_reviews.require_review(task['id'], result_id)
            _, refreshed = await runtime.result_reviews.context(task['id'])
            assert set(next(iter(deliveries.values()))) != set(next(iter(refreshed.values())))
            runtime.result_reviews.delivered(task['id'], refreshed)
            runtime.result_reviews.require_review(task['id'], result_id)
            assert store.get_document(before['id']) == changed
    asyncio.run(check())


def test_conflict_in_later_action_is_detected_before_any_submission(tmp_path):
    async def check():
        async with workspace(tmp_path) as (store, runtime, session, task):
            document_id = session['document']['id']
            first = store.commit_action(document_id, {'kind': 'create_element', 'args': {'type': 'rectangle'}}, {})['changed_ids'][0]
            second = store.commit_action(document_id, {'kind': 'create_element', 'args': {'type': 'ellipse', 'x': 100}}, {})['changed_ids'][0]
            result_id = publish_result(store, runtime, session, task, [
                ('move_elements', {'ids': [first], 'dx': 10, 'dy': 0}),
                ('move_elements', {'ids': [second], 'dx': 20, 'dy': 0})])
            before = store.commit_action(document_id, {'kind': 'move_elements', 'args': {'ids': [second], 'dx': 3, 'dy': 0}}, {})['document']
            with pytest.raises(VersionConflict, match=second):
                runtime.result_reviews.proposed(task['id'], result_id)
            output = await main_tool(runtime, store, session, task, 'apply_agent_result', {'result_id': result_id}, 'conflict')
            assert output['ok'] is False and second in output['error']
            assert store.get_document(document_id) == before
            assert store.agents.get_result(session['id'], result_id)['committed_count'] == 0
    asyncio.run(check())


def test_explicit_preview_selects_another_result_and_partial_commit_requires_new_review(tmp_path):
    async def check():
        async with workspace(tmp_path) as (store, runtime, session, task):
            first_id = publish_result(store, runtime, session, task, [('create_element', {'type': 'rectangle'})], 'first')
            second_id = publish_result(store, runtime, session, task, [
                ('create_element', {'type': 'ellipse', 'x': 200}), ('create_element', {'type': 'diamond', 'x': 350})], 'second')
            output = await main_tool(runtime, store, session, task, 'view_agent_result', {'result_id': second_id,
                'region': {'x': 150, 'y': -20, 'width': 400, 'height': 200}}, 'choose-second')
            assert output['ok'] and output['scope'] == 'proposed_result'
            _, deliveries = await runtime.result_reviews.context(task['id'])
            assert len(next(iter(deliveries.values()))) == 2
            runtime.result_reviews.delivered(task['id'], deliveries)
            runtime.result_reviews.require_review(task['id'], second_id)
            with pytest.raises(ValueError):
                runtime.result_reviews.require_review(task['id'], first_id)
            result = store.agents.get_result(session['id'], second_id)
            item = result['actions'][0]
            store.commit_action(session['document']['id'], item['action'], item['expected_elements'], (second_id, 0))
            with pytest.raises(ValueError):
                runtime.result_reviews.require_review(task['id'], second_id)
            _, resumed = await runtime.result_reviews.context(task['id'])
            runtime.result_reviews.delivered(task['id'], resumed)
            output = await main_tool(runtime, store, session, task, 'apply_agent_result', {'result_id': second_id}, 'resume-second')
            assert output['ok'] and len(store.get_document(session['document']['id'])['elements']) == 2
    asyncio.run(check())


def test_clear_result_requires_a_rendered_empty_preview_and_stays_task_scoped(tmp_path):
    async def check():
        async with workspace(tmp_path) as (store, runtime, session, task):
            element_id = store.commit_action(session['document']['id'], {'kind': 'create_element', 'args': {'type': 'rectangle'}}, {})['changed_ids'][0]
            result_id = publish_result(store, runtime, session, task, [('delete_elements', {'ids': [element_id]})])
            with pytest.raises(ValueError):
                runtime.result_reviews.require_review(task['id'], result_id)
            _, deliveries = await runtime.result_reviews.context(task['id'])
            assert deliveries
            runtime.result_reviews.delivered(task['id'], deliveries)
            runtime.result_reviews.require_review(task['id'], result_id)
            future = store.create_task(session['id'])
            future_id = publish_result(store, runtime, session, future, [('create_element', {'type': 'ellipse'})], 'future')
            with pytest.raises(ValueError, match='later task'):
                runtime.result_reviews.proposed(task['id'], future_id)
            other = store.create_session('其他画布')
            other_task = store.create_task(other['id'])
            with pytest.raises(KeyError):
                runtime.result_reviews.proposed(other_task['id'], result_id)
            with pytest.raises(ValueError, match='cannot create or delegate'):
                validate_tool('view_agent_result', {'result_id': result_id}, child=True)
    asyncio.run(check())
