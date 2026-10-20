import asyncio
import base64
import copy
import io
import json
from pathlib import Path

import pytest
from PIL import Image

from studio.actions import element_fingerprint
from studio.agents import DrawingAgents, prepare_drawing_tool
from studio.runtime import DrawingRuntime
from studio.settings import ModelConfig, ModelSettings
from studio.store import StudioStore


@pytest.mark.parametrize('manual_edit', [False, True])
@pytest.mark.parametrize('competing_result', [False, True])
def test_latest_private_workspace_replaces_stale_repair_ids_without_approving_submission(tmp_path, native_drawing_child, manual_edit, competing_result):
    async def check():
        store = StudioStore(tmp_path / 'private-workspace.sqlite3')
        runtime = DrawingRuntime(store, ModelSettings(store))
        session = store.create_session()
        task = store.create_task(session['id'])
        document_id = session['document']['id']
        config = ModelConfig(provider='ollama', model='llama3.1', api_base='http://localhost:11434')
        models = DrawingAgents(store, config)
        receipts = json.loads((Path(__file__).parent / 'fixtures/ineffective-cleanup-reviews.json').read_text(encoding='utf-8'))['reviews']

        async def main_tool(name, arguments, call_id):
            return await runtime.call_main_tool(models, task['id'], session['id'], document_id,
                {'id': call_id, 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(arguments)}})

        def completion(receipt, ids):
            # 真实评审记录仅用于上下文路由检查，测试图形不参与画质评分。
            findings = copy.deepcopy(receipt['assessment']['findings'])
            for finding in findings:
                finding['object_ids'] = ids
            return {'status': 'blocked', 'reason': 'ineffective_repair',
                'visible_state': receipt['assessment']['visible_state'], 'findings': findings,
                'next_strategy': receipt['assessment']['revision']['next_strategy']}

        try:
            marker = store.commit_action(document_id, {'kind': 'create_element', 'args': {
                'type': 'rectangle', 'x': 500, 'name': '保留标记'}}, {})['changed_ids'][0]
            if competing_result:
                records = store.agents
                other_agent = records.create_agent(session['id'], task['id'], 'independent', '准备另一部分', 'Other', [])
                other_run = runtime.children._initialize(session['id'], records.next_run(
                    session['id'], other_agent['agent_id'], task['id']))
                request = {'type': 'ellipse', 'x': 900}
                assert records.begin_call(task['id'], other_run['id'], 'independent-outline',
                    'create_element', json.dumps(request)) is None
                draft, selection, actions, output = prepare_drawing_tool('create_element', request,
                    other_run['document'], other_run['draft'], other_run['selection'])
                other_run.update(draft=draft, selection=selection, prepared=actions)
                records.finish_call(other_run['id'], 'independent-outline', output, other_run)
                records.complete_run(session['id'], other_run['id'], '另一候选待审阅')
            first = native_drawing_child(store, runtime, session, task)
            old_id = first.call('create_element', {'type': 'ellipse'}, 'old-outline')['changed_ids'][0]
            old = store.agents.complete_run(session['id'], first.run['id'], '原候选',
                completion=completion(receipts[-2], [old_id]))
            assert (await main_tool('view_agent_result', {'result_id': old['result_id']}, 'earlier-view'))['ok']

            latest = native_drawing_child(store, runtime, session, task, first.agent)
            assert latest.call('delete_elements', {'ids': [old_id]}, 'remove-old')['ok']
            new_id = latest.call('create_element', {'type': 'diamond', 'x': 200}, 'new-outline')['changed_ids'][0]
            full = await runtime.vision.observe(task['id'], latest.agent['agent_id'], latest.run['id'], latest.document)
            closeup = await runtime.vision.observe(task['id'], latest.agent['agent_id'], latest.run['id'], latest.document,
                {'x': 180, 'y': -20, 'width': 160, 'height': 130})
            runtime.vision.delivered(task['id'], latest.run['id'], [full['observation_id'], closeup['observation_id']])
            result = store.agents.complete_run(session['id'], latest.run['id'], '最新候选',
                completion=completion(receipts[-1], [new_id]))
            if manual_edit:
                store.commit_action(document_id, {'kind': 'create_element', 'args': {
                    'type': 'ellipse', 'x': 700, 'name': '后续手动编辑'}}, {})
            before = store.get_document(document_id)
            history = store.get_history(document_id, 'undo')
            if competing_result:
                failed_view = await main_tool('view_agent_result', {'result_id': result['result_id']}, 'blocked-preview')
                assert failed_view['ok'] is False and 'dependency result first' in failed_view['error']
            deliveries_before = [tuple(row) for row in store.connection.execute(
                'SELECT id,delivered_at FROM canvas_observations ORDER BY id')]
            messages, deliveries = await runtime.result_reviews.context(task['id'])
            heading = json.loads(messages[0]['content'])
            assert heading['pending_private_workspace']['result_id'] == result['result_id']
            assert heading['shared_canvas_changed'] is manual_edit
            assert heading['native_submission_preview_available'] is False
            assert old['result_id'] in heading['unavailable_previews'][0]['error']
            repair = json.loads(messages[-1]['content'])['private_repair_workspace']
            assert repair['result_id'] == result['result_id']
            assert repair['continue_with']['relevant_ids'] == [new_id]
            assert old_id not in json.dumps(repair)
            images = [message['content'] for message in messages if isinstance(message['content'], list)]
            assert len(images) == 2 and deliveries == {}
            for content in images:
                evidence = json.loads(content[0]['text'])['private_workspace_image']
                assert evidence['run_id'] == latest.run['id'] and evidence['read_only'] is True
                encoded = content[1]['image_url']['url'].split(',', 1)[1]
                assert base64.b64decode(encoded) == runtime.vision.image(session['id'], evidence['observation_id'])
            assert store.get_document(document_id) == before and store.get_history(document_id, 'undo') == history
            assert deliveries_before == [tuple(row) for row in store.connection.execute(
                'SELECT id,delivered_at FROM canvas_observations ORDER BY id')]
            if competing_result:
                error_context = runtime.memory.prepare_messages(task['id'], 'main', f"main:{task['id']}",
                    'current-state', before, [])
                assert any(message.get('tool_call_id') == 'blocked-preview'
                    and json.loads(message['content']) == failed_view for message in error_context)
                runtime.memory.delivered(f"main:{task['id']}", error_context)
            runtime.result_reviews.delivered(task['id'], deliveries)
            if not competing_result:
                invalid = await main_tool('view_agent_result', {'result_id': result['result_id'],
                    'region': {'x': 0, 'y': 0, 'width': -1, 'height': 80}}, 'invalid-region')
                assert invalid['ok'] is False
                unchanged, unapproved = await runtime.result_reviews.context(task['id'])
                assert json.loads(unchanged[0]['content'])['pending_private_workspace']['result_id'] == result['result_id']
                assert unapproved == {}
                error_context = runtime.memory.prepare_messages(task['id'], 'main', f"main:{task['id']}",
                    'current-state', before, [])
                assert any(message.get('tool_call_id') == 'invalid-region'
                    and json.loads(message['content']) == invalid for message in error_context)
                runtime.memory.delivered(f"main:{task['id']}", error_context)
            with pytest.raises(ValueError, match='dependency result first'):
                runtime.result_reviews.require_review(task['id'], result['result_id'])
            with pytest.raises(ValueError, match='Inspect this proposed child result'):
                runtime.result_reviews.require_review(task['id'], old['result_id'])
            with pytest.raises(ValueError, match='another Agent/task'):
                runtime.vision.ground_tool(task['id'], f"main:{task['id']}", before, 'erase_strokes',
                    {'observation_id': full['observation_id'], 'points': [[500, 500]], 'radius': 20})
            other = store.create_task(session['id'])
            with pytest.raises(ValueError, match='another task'):
                runtime.result_reviews.private_workspaces.context(other['id'], store.agents.get_result(
                    session['id'], result['result_id']), [])

            assert (await main_tool('view_agent_result', {'result_id': old['result_id']}, 'explicit-dependency'))['ok']
            dependency_messages, reviewed = await runtime.result_reviews.context(task['id'])
            dependency = json.loads(dependency_messages[0]['content'])
            assert dependency['proposed_result']['result_id'] == old['result_id']
            assert dependency['newer_private_workspace_result_id'] == result['result_id']
            assert not any('private_repair_workspace' in message.get('content', '')
                for message in dependency_messages if isinstance(message.get('content'), str))
            runtime.result_reviews.delivered(task['id'], reviewed)
            assert (await main_tool('apply_agent_result', {'result_id': old['result_id']}, 'apply-dependency'))['ok']
            with pytest.raises(ValueError, match='Inspect this proposed child result'):
                runtime.result_reviews.require_review(task['id'], result['result_id'])
            fresh, reviewed = await runtime.result_reviews.context(task['id'])
            assert json.loads(fresh[0]['content'])['proposed_result']['result_id'] == result['result_id']
            proposed_png = runtime.vision.image(session['id'], next(iter(reviewed.values()))[0])
            runtime.result_reviews.delivered(task['id'], reviewed)
            assert (await main_tool('apply_agent_result', {'result_id': result['result_id']}, 'apply-latest'))['ok']
            after = store.get_document(document_id)
            visible = {item['id']: item for item in after['elements'] if not item.get('isDeleted')}
            assert old_id not in visible and new_id in visible
            for item in before['elements']:
                assert element_fingerprint(visible[item['id']]) == element_fingerprint(item)
            assert marker in visible
            rendered = await runtime.vision.renderer.render(after)
            with Image.open(io.BytesIO(proposed_png)) as proposed, Image.open(io.BytesIO(rendered['png'])) as actual:
                assert proposed.size == actual.size and proposed.tobytes() == actual.tobytes()
        finally:
            await runtime.close()
            store.close()
    asyncio.run(check())
