import asyncio
import hashlib
import json
from pathlib import Path

import pytest

from studio.actions import create_native_element
from studio.agents import DrawingAgents, main_replan_ids
from studio.completion import parse_reply_assessment
from studio.quality import review_scene_key
from studio.runtime import DrawingRuntime
from studio.settings import ModelConfig, ModelSettings
from studio.store import StudioStore


def workspace(tmp_path):
    store = StudioStore(tmp_path / 'completion.sqlite3')
    runtime = DrawingRuntime(store, ModelSettings(store))
    session = store.create_session()
    task = store.create_task(session['id'])
    models = DrawingAgents(store,
        ModelConfig(provider='ollama', model='llama3.1', api_base='http://localhost:11434'))
    return store, runtime, session, task, models


@pytest.mark.parametrize('content', [None, '直接说明当前画布。', '{unstructured text', '[]', 'null',
    [{'type': 'text', 'text': '描述附带的图片。'}], json.dumps({'child_agents': None})])
def test_optional_context_annotations_accept_regular_and_multimodal_messages(content):
    assert main_replan_ids([{'role': 'system', 'content': '说明画布。'}, {'role': 'user', 'content': content}]) == []
    assert main_replan_ids([{'role': 'user', 'content': content}]) == []


@pytest.mark.parametrize('name', ['status', 'rectangle', 'hallucinated-state'])
def test_actual_recorded_reply_reviews_preserve_routes_and_rejections(name):
    fixture = json.loads((Path(__file__).parent / 'fixtures/reply-completion-responses.json').read_text(encoding='utf-8'))
    case = next(item for item in fixture['cases'] if item['name'] == name)
    raw = case['response']['tool_calls'][0]['function']['arguments']
    assert hashlib.sha256(raw.encode()).hexdigest() == case['arguments_sha256']
    assessment, error = parse_reply_assessment(case['response'])
    assert error is None
    assert assessment.requested_output == case['expected_route']
    assert assessment.accepted == case['expected_acceptance']


def test_actual_other_review_function_is_returned_in_full_without_reply_approval():
    fixture = json.loads((Path(__file__).parent / 'fixtures/quality-local-edit-response.json').read_text(encoding='utf-8'))
    assessment, error = parse_reply_assessment(fixture['response'])
    assert assessment is None and error['ok'] is False
    assert error['review_response'] == fixture['response']


def test_reply_evidence_uses_actual_current_task_errors_and_preserved_canvas(tmp_path):
    async def check():
        store, runtime, session, task, models = workspace(tmp_path)
        try:
            request = '报告当前检查失败的原因，保持画布。'
            store.add_message(session['id'], 'user', request, task['id'])
            runtime.quality.begin(task['id'], session['document'], request)
            failure = await runtime.call_main_tool(models, task['id'], session['id'], session['document']['id'], {
                'id': 'missing', 'function': {'name': 'inspect_scene', 'arguments': json.dumps({'ids': ['missing']})}})
            assert failure['ok'] is False
            evidence = runtime.reply_completion.candidate(task['id'])
            assert evidence['current_user_request'] == request and evidence['canvas_preserved']
            assert evidence['canvas_state'] == {'visible_object_count': 0, 'selected_ids': []}
            assert evidence['tool_errors'] == [{'call_id': 'missing', 'name': 'inspect_scene', 'output': failure}]
            other = store.create_session()
            other_task = store.create_task(other['id'])
            await runtime.call_main_tool(models, other_task['id'], other['id'], other['document']['id'], {
                'id': 'foreign', 'function': {'name': 'inspect_scene', 'arguments': json.dumps({'ids': ['foreign']})}})
            assert runtime.reply_completion.candidate(task['id']) == evidence
            blocked = await runtime.call_main_tool(models, task['id'], session['id'], session['document']['id'], {
                'id': 'finish-error', 'function': {'name': 'finish_drawing', 'arguments': json.dumps({'summary': '说明'})}},
                completion_error='Read every tool error before completing.')
            assert not blocked['ok'] and runtime.reply_completion.candidate(task['id']) == evidence
        finally:
            await runtime.close()
            store.close()
    asyncio.run(check())


@pytest.mark.parametrize('operation', ['move', 'selection', 'failed-edit', 'move-and-undo'])
def test_canvas_edits_and_attempts_keep_the_visual_completion_route(tmp_path, operation):
    async def check():
        store, runtime, session, task, models = workspace(tmp_path)
        try:
            element = create_native_element({'type': 'ellipse', 'x': 20, 'y': 30})
            initial = store.save_snapshot(session['document']['id'], 0, [element], [])
            runtime.quality.begin(task['id'], initial, '修改圆形后说明。')
            assert runtime.reply_completion.candidate(task['id']) is not None
            name, arguments = ('select_elements', {'ids': [element['id']]}) if operation == 'selection' else (
                'move_elements', {'ids': ['missing'] if operation == 'failed-edit' else [element['id']], 'dx': 15, 'dy': 0})
            result = await runtime.call_main_tool(models, task['id'], session['id'], initial['id'], {
                'id': operation, 'function': {'name': name, 'arguments': json.dumps(arguments)}})
            assert result['ok'] == (operation != 'failed-edit')
            if operation == 'move-and-undo':
                restored = store.history_step(initial['id'], 'undo')
                assert review_scene_key(restored) == review_scene_key(initial)
            assert runtime.reply_completion.candidate(task['id']) is None
        finally:
            await runtime.close()
            store.close()
    asyncio.run(check())


def test_private_drawing_keeps_visual_review_after_the_candidate_is_rejected(tmp_path, native_drawing_child):
    async def check():
        store, runtime, session, task, _ = workspace(tmp_path)
        try:
            runtime.quality.begin(task['id'], session['document'], '画一个圆。')
            child = native_drawing_child(store, runtime, session, task)
            assert child.call('create_element', {'type': 'ellipse'}, 'circle')['ok']
            result = store.agents.complete_run(session['id'], child.run['id'], '已准备圆形')
            store.agents.reject_result(session['id'], result['result_id'], '当前结果未采用')
            assert runtime._child_work_settled(session['id'], task['id'])
            assert not store.get_document(session['document']['id'])['elements']
            assert runtime.reply_completion.candidate(task['id']) is None
        finally:
            await runtime.close()
            store.close()
    asyncio.run(check())
