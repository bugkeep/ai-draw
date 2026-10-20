import asyncio
import io
import json

import pytest
from PIL import Image, ImageDraw

from studio.agents import DrawingAgents, compact_drawing_exchange
from studio.agents import prepare_drawing_tool
from studio.runtime import DrawingRuntime
from studio.reference_images import decode_reference
from studio.settings import ModelConfig, ModelSettings
from studio.store import StudioStore


def workspace(path):
    store = StudioStore(path)
    runtime = DrawingRuntime(store, ModelSettings(store))
    session = store.create_session('持久工作记忆')
    task = store.create_task(session['id'])
    models = DrawingAgents(store, ModelConfig(provider='ollama', model='llama3.1', api_base='http://localhost:11434'))
    return store, runtime, session, task, models


async def call(runtime, models, session, task, name, arguments, call_id):
    return await runtime.call_main_tool(models, task['id'], session['id'], session['document']['id'], {
        'id': call_id, 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(arguments)}})


def test_large_geometry_is_compressed_and_exact_points_and_raw_output_remain_retrievable(tmp_path, native_drawing_child):
    async def check():
        store, runtime, session, task, models = workspace(tmp_path / 'geometry.sqlite3')
        try:
            child = native_drawing_child(store, runtime, session, task)
            created = child.call('create_element', {
                'type': 'freedraw', 'name': '较早的面部轮廓', 'points': [[index, index % 30] for index in range(900)],
            }, 'create')
            await child.submit()
            element_id = created['changed_ids'][0]
            args = {'ids': [element_id], 'include_geometry': True}
            output = await call(runtime, models, session, task, 'inspect_scene', args, 'inspect')
            document = store.get_document(session['document']['id'])
            compact = runtime.memory.for_context(task['id'], 'main', f"main:{task['id']}", 'inspect',
                'inspect_scene', json.dumps(args), output, f"{document['id']}:{document['version']}")
            assert compact['compressed'] and len(json.dumps(compact)) < 7000
            assert '"points"' not in json.dumps(compact)
            assert len(output['scene']['elements'][0]['points']) == 900
            restored, point_offset = [], 0
            while point_offset is not None:
                page = runtime.memory.recall(task['id'], 'main', {'element_id': element_id,
                    'point_offset': point_offset, 'point_limit': 256}, document)
                restored.extend(page['element']['points'])
                point_offset = page['next_point_offset']
            assert restored == document['elements'][0]['points']
            chunks, offset = [], 0
            while offset is not None:
                page = runtime.memory.recall(task['id'], 'main', {'memory_ref': compact['memory_ref'],
                    'section': 'output', 'offset': offset, 'max_chars': 12000}, document)
                chunks.append(page['text'])
                offset = page['next_offset']
            assert json.loads(''.join(chunks)) == output
            found = runtime.memory.search(task['id'], 'main', {'query': '面部'}, document)
            assert found['objects'][0]['id'] == element_id and found['records']
        finally:
            await runtime.close()
            store.close()
    asyncio.run(check())


def test_unread_errors_survive_history_removal_and_delivery_survives_restart(tmp_path):
    async def check():
        path = tmp_path / 'errors.sqlite3'
        store, runtime, session, task, models = workspace(path)
        invalid = {'id': 'invalid-json', 'type': 'function', 'function': {'name': 'move_elements', 'arguments': '{'}}
        output = await runtime.call_main_tool(models, task['id'], session['id'], session['document']['id'], invalid)
        assert output['ok'] is False
        caller = f"main:{task['id']}"
        document = store.get_document(session['document']['id'])
        messages = runtime.memory.prepare_messages(task['id'], 'main', caller, 'current', document, [])
        errors = [item for item in messages if item.get('role') == 'tool']
        assert len(errors) == 1 and json.loads(errors[0]['content']) == output
        assert messages[-2]['tool_calls'][0] == invalid
        runtime.memory.delivered(caller, messages)
        assert not any(item.get('role') == 'tool' for item in runtime.memory.prepare_messages(
            task['id'], 'main', caller, 'current', document, []))
        await runtime.close()
        store.close()
        reopened = StudioStore(path)
        continued = DrawingRuntime(reopened, ModelSettings(reopened))
        try:
            assert not any(item.get('role') == 'tool' for item in continued.memory.prepare_messages(
                task['id'], 'main', caller, 'current', document, []))
        finally:
            await continued.close()
            reopened.close()
    asyncio.run(check())


def test_working_notes_and_search_exclude_future_tasks_and_other_scopes(tmp_path):
    async def check():
        store, runtime, session, task, models = workspace(tmp_path / 'notes.sqlite3')
        other = store.create_session('独立会话')
        other_task = store.create_task(other['id'])
        document = store.get_document(session['document']['id'])
        try:
            old = runtime.memory.compact(task['id'], 'main', {'expected_revision': 0,
                'goal': '修正人物轮廓', 'summary': '已检查面部，等待调整右手', 'pending': ['调整右手']}, document)
            future = store.create_task(session['id'])
            store.add_message(session['id'], 'user', 'FUTURE_ONLY_禁止进入较早任务', future['id'])
            new = runtime.memory.compact(future['id'], 'main', {'expected_revision': 1,
                'goal': 'FUTURE_ONLY_未来任务', 'summary': '等待执行'}, document)
            snapshot = runtime.memory.snapshot(task['id'], 'main', f"main:{task['id']}", 'state', document)
            assert snapshot['working_memory']['memory_ref'] == old['memory_ref']
            assert snapshot['working_memory']['revision'] == 1
            found = runtime.memory.search(task['id'], 'main', {'query': 'FUTURE_ONLY'}, document)
            assert found['records'] == []
            with pytest.raises(KeyError, match='later task'):
                runtime.memory.recall(task['id'], 'main', {'memory_ref': new['memory_ref']}, document)
            with pytest.raises(KeyError, match='Agent and canvas'):
                runtime.memory.recall(other_task['id'], 'main', {'memory_ref': old['memory_ref']}, other['document'])
            with pytest.raises(KeyError, match='Agent and canvas'):
                runtime.memory.recall(task['id'], 'agent-other', {'memory_ref': old['memory_ref']}, document)
            with pytest.raises(ValueError, match='revision'):
                runtime.memory.compact(future['id'], 'main', {'expected_revision': 0,
                    'goal': '重复更新', 'summary': '不应覆盖'}, document)
            assert runtime.memory.snapshot(future['id'], 'main', 'later', 'state', document)['working_memory']['revision'] == 2
        finally:
            await runtime.close()
            store.close()
    asyncio.run(check())


def test_query_memory_survives_compaction_and_is_invalidated_by_canvas_changes(tmp_path):
    async def check():
        store, runtime, session, task, models = workspace(tmp_path / 'queries.sqlite3')
        try:
            created = await call(runtime, models, session, task, 'create_element', {'type': 'ellipse', 'name': '左眼'}, 'eye')
            element_id = created['changed_ids'][0]
            args = {'ids': [element_id], 'include_geometry': False}
            for index in range(2):
                await call(runtime, models, session, task, 'inspect_scene', args, f'inspect-{index}')
            before = store.get_document(session['document']['id'])
            invalid = await call(runtime, models, session, task, 'compact_context', {
                'goal': '调整左眼', 'summary': '错误身份必须反馈', 'object_ids': [element_id, 'agent-example'],
            }, 'invalid-memory-identity')
            assert invalid['ok'] is False and 'agent-example' in invalid['error'] and 'summary or constraints' in invalid['error']
            assert store.get_document(before['id']) == before
            await call(runtime, models, session, task, 'compact_context', {
                'goal': '调整左眼', 'summary': '已查阅左眼位置', 'pending': ['移动左眼'], 'object_ids': [element_id],
            }, 'compact')
            document = store.get_document(session['document']['id'])
            snapshot = runtime.memory.snapshot(task['id'], 'main', f"main:{task['id']}",
                f"{document['id']}:{document['version']}", document)
            assert snapshot['known_queries_in_current_state'][0]['repeat_count'] == 2
            assert snapshot['known_queries_in_current_state'][0]['request'] == args
            assert snapshot['working_memory']['notes']['pending'] == ['移动左眼']
            await call(runtime, models, session, task, 'move_elements', {'ids': [element_id], 'dx': 10, 'dy': 0}, 'move')
            current = store.get_document(document['id'])
            assert runtime.memory.snapshot(task['id'], 'main', f"main:{task['id']}",
                f"{current['id']}:{current['version']}", current)['known_queries_in_current_state'] == []
            store.delete_session(session['id'])
            assert store.connection.execute('SELECT count(*) FROM agent_memories').fetchone()[0] == 0
            assert store.connection.execute('SELECT count(*) FROM agent_working_memory').fetchone()[0] == 0
        finally:
            await runtime.close()
            store.close()
    asyncio.run(check())


def test_equivalent_read_evidence_folds_without_losing_pages_edits_errors_or_scope(tmp_path):
    async def check():
        store, runtime, session, task, models = workspace(tmp_path / 'read-evidence.sqlite3')
        caller = f"main:{task['id']}"
        messages = [{'role': 'user', 'content': '保留原对象，检查后把左眼向右移动。'}]

        async def exchange(name, arguments, call_id):
            output = await call(runtime, models, session, task, name, arguments, call_id)
            function = {'id': call_id, 'type': 'function', 'function': {
                'name': name, 'arguments': json.dumps(arguments)}}
            messages.extend([{'role': 'assistant', 'tool_calls': [function]},
                             {'role': 'tool', 'tool_call_id': call_id, 'content': json.dumps(output)}])
            return output

        try:
            created = await exchange('create_elements', {'items': [
                {'type': 'ellipse', 'name': '左眼', 'x': 20, 'y': 30},
                {'type': 'ellipse', 'name': '右眼', 'x': 70, 'y': 30}]}, 'eyes')
            element_id = created['changed_ids'][0]
            page = await exchange('inspect_scene', {'offset': 0, 'limit': 1}, 'page-first')
            duplicate = await exchange('inspect_scene', {'offset': 0, 'limit': 1,
                'query': '', 'ids': [], 'include_geometry': False}, 'page-equivalent')
            assert page == duplicate
            await exchange('inspect_scene', page['scene']['next_page'], 'page-second')
            invalid = await exchange('move_elements', {'ids': ['missing-eye'], 'dx': 10, 'dy': 0}, 'bad-move')
            assert invalid['ok'] is False
            moved = await exchange('move_elements', {'ids': [element_id], 'dx': 10, 'dy': 0}, 'move-eye')
            assert moved['ok'] is True
            await exchange('inspect_scene', {'offset': 0, 'limit': 1}, 'changed-page')
            await exchange('inspect_scene', {'offset': 0, 'limit': 1}, 'latest-page')
            original = json.dumps(messages)
            document = store.get_document(session['document']['id'])
            compact = runtime.memory.prepare_messages(task['id'], 'main', caller,
                f"{document['id']}:{document['version']}", document, messages)
            ids = [item['tool_call_id'] for item in compact if item.get('role') == 'tool']
            assert ids == ['eyes', 'page-equivalent', 'page-second', 'bad-move', 'move-eye', 'latest-page']
            assert next(item['content'] for item in compact if item.get('tool_call_id') == 'bad-move') == json.dumps(invalid)
            assert compact[0] == messages[0] and json.dumps(messages) == original
            snapshot = json.loads(compact[-1]['content'])
            assert snapshot['known_queries_in_current_state'][0]['repeat_count'] == 2
            assert len(store.connection.execute('SELECT * FROM agent_tool_calls').fetchall()) == 8
            assert store.get_document(document['id']) == document
            # A foreign or later caller cannot reinterpret this caller's history.
            future = store.create_task(session['id'])
            assert runtime.memory.fold_reads(f"main:{future['id']}", messages) == messages
            future_output = await call(runtime, models, session, future, 'inspect_scene', {'offset': 0, 'limit': 1}, 'future-page')
            future_context = runtime.memory.for_context(future['id'], 'main', f"main:{future['id']}",
                'future-page', 'inspect_scene', json.dumps({'offset': 0, 'limit': 1}), future_output,
                f"{document['id']}:{document['version']}")
            assert future_context['same_state_query_count'] == 1
            runtime.memory.delivered(caller, compact)
            recalled = runtime.memory.recall(task['id'], 'main', {
                'memory_ref': store.connection.execute('SELECT id FROM agent_memories WHERE caller_id=? AND call_id=?',
                    (caller, 'page-first')).fetchone()[0], 'section': 'output'}, document)
            assert json.loads(recalled['text']) == page
        finally:
            await runtime.close()
            store.close()
    asyncio.run(check())


def test_cached_canvas_reads_share_evidence_and_keep_the_latest_image_receipt(tmp_path):
    async def check():
        store, runtime, session, task, models = workspace(tmp_path / 'cached-reads.sqlite3')
        try:
            await call(runtime, models, session, task, 'create_element', {'type': 'ellipse'}, 'circle')
            outputs = [await call(runtime, models, session, task, 'view_canvas', {}, call_id)
                       for call_id in ['view-first', 'view-cached']]
            assert outputs[0]['observation_id'] == outputs[1]['observation_id'] and outputs[1]['cached']
            messages = [item for call_id, output in zip(['view-first', 'view-cached'], outputs)
                        for item in [{'role': 'assistant', 'tool_calls': [{'id': call_id, 'type': 'function',
                            'function': {'name': 'view_canvas', 'arguments': '{}'}}]},
                                     {'role': 'tool', 'tool_call_id': call_id, 'content': json.dumps(output)}]]
            compact = runtime.memory.fold_reads(f"main:{task['id']}", messages)
            assert compact == messages[2:]
            records = store.connection.execute('SELECT query_key FROM agent_memories WHERE tool_name=?', ('view_canvas',)).fetchall()
            assert len(records) == 2 and records[0]['query_key'] == records[1]['query_key']
        finally:
            await runtime.close()
            store.close()
    asyncio.run(check())


def test_child_read_folding_keeps_mixed_writes_and_errors_and_does_not_suggest_delegation(tmp_path, native_drawing_child):
    async def check():
        store, runtime, session, task, models = workspace(tmp_path / 'child-reads.sqlite3')
        try:
            child = native_drawing_child(store, runtime, session, task)
            child.call('create_element', {'type': 'ellipse', 'name': '私有轮廓'}, 'outline')
            calls, outputs = [], []
            for name, args, call_id in [('inspect_scene', {'limit': 1}, 'inspect-old'),
                    ('inspect_scene', {'limit': 1}, 'inspect-latest'),
                    ('move_elements', {'ids': ['missing'], 'dx': 10, 'dy': 0}, 'bad-edit')]:
                output = child.call(name, args, call_id)
                calls.append({'id': call_id, 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}})
                outputs.append({'role': 'tool', 'tool_call_id': call_id, 'content': json.dumps(output)})
            messages = [{'role': 'assistant', 'content': '检查完成后清理当前轮廓。', 'tool_calls': calls}, *outputs]
            compact = runtime.memory.fold_reads(child.run['id'], messages)
            assert [item['id'] for item in compact[0]['tool_calls']] == ['inspect-latest', 'bad-edit']
            assert compact[0]['content'] == messages[0]['content'] and compact[-1] == outputs[-1]
            context = runtime.memory.for_context(task['id'], child.agent['agent_id'], child.run['id'],
                'inspect-latest', 'inspect_scene', json.dumps({'limit': 1}), json.loads(outputs[1]['content']),
                f"{child.run['id']}:{len(child.run['prepared'])}")
            assert 'You cannot delegate' in context['retrieval'] and 'Delegate extensive' not in context['retrieval']
            # Unmatched batches and duplicate protocol IDs are preserved verbatim.
            assert runtime.memory.fold_reads(child.run['id'], messages[:-1]) == messages[:-1]
            duplicate_ids = [*messages, *messages]
            assert runtime.memory.fold_reads(child.run['id'], duplicate_ids) == duplicate_ids
            renamed = [{**messages[0], 'tool_calls': [{**calls[0], 'function': {
                **calls[0]['function'], 'name': 'delete_elements'}}, *calls[1:]]}, *outputs]
            assert runtime.memory.fold_reads(child.run['id'], renamed) == renamed
        finally:
            await runtime.close()
            store.close()
    asyncio.run(check())


def test_mixed_exchange_archives_successful_parameters_and_preserves_full_error(tmp_path, native_drawing_child):
    async def check():
        store, runtime, session, task, models = workspace(tmp_path / 'mixed.sqlite3')
        try:
            good = {'id': 'draw', 'type': 'function', 'function': {'name': 'create_element', 'arguments': json.dumps({
                'type': 'freedraw', 'points': [[index, index % 9] for index in range(700)]})}}
            bad = {'id': 'missing', 'type': 'function', 'function': {'name': 'move_elements', 'arguments': json.dumps({
                'ids': ['missing'], 'dx': 10, 'dy': 0})}}
            child = native_drawing_child(store, runtime, session, task)
            outputs = [child.call(item['function']['name'], json.loads(item['function']['arguments']), item['id'])
                       for item in (good, bad)]
            exchange = [{'role': 'assistant', 'tool_calls': [good, bad]},
                        *[{'role': 'tool', 'tool_call_id': item['id'], 'content': json.dumps(output)}
                          for item, output in zip((good, bad), outputs)]]
            compact = compact_drawing_exchange(exchange)
            assert compact[0]['role'] == 'user' and 'completed_drawing_tools' in json.loads(compact[0]['content'])
            assert compact[1]['tool_calls'] == [bad]
            assert json.loads(compact[2]['content']) == outputs[1]
        finally:
            await runtime.close()
            store.close()
    asyncio.run(check())


def test_child_notes_and_tool_memory_survive_shutdown_with_private_scope(tmp_path):
    async def check():
        path = tmp_path / 'child.sqlite3'
        store, runtime, session, task, models = workspace(path)
        child = store.agents.create_agent(session['id'], task['id'], 'child', '准备眼睛轮廓', 'Artist', [])
        run = runtime.children._initialize(session['id'], store.agents.next_run(session['id'], child['agent_id'], task['id']))
        arguments = {'paths': [{'name': '私有眼睛', 'points': [[10, 20], [30, 10], [50, 20]]}]}
        encoded = json.dumps(arguments)
        store.agents.begin_call(task['id'], run['id'], 'draw', 'draw_paths', encoded)
        draft, selection, prepared, output = prepare_drawing_tool('draw_paths', arguments, run['document'], run['draft'], [])
        run.update(draft=draft, selection=selection, prepared=prepared)
        store.agents.finish_call(run['id'], 'draw', output, run)
        memory_ref = runtime.memory.register(task['id'], child['agent_id'], run['id'], 'draw', 'draw_paths',
                                             encoded, output, f"{run['id']}:1")
        document = {**run['document'], 'elements': draft, 'selection': selection}
        saved = runtime.memory.compact(task['id'], child['agent_id'], {
            'goal': '准备眼睛轮廓', 'summary': '已准备一条曲线，等待检查', 'object_ids': output['changed_ids'],
            'evidence_refs': [memory_ref], 'pending': ['检查草稿图片'],
        }, document)
        store.agents.pause_agent(session['id'], child['agent_id'])
        await runtime.close()
        store.close()
        reopened = StudioStore(path)
        continued = DrawingRuntime(reopened, ModelSettings(reopened))
        try:
            snapshot = continued.memory.snapshot(task['id'], child['agent_id'], run['id'], f"{run['id']}:1", document)
            assert snapshot['working_memory']['memory_ref'] == saved['memory_ref']
            assert snapshot['working_memory']['notes']['pending'] == ['检查草稿图片']
            recalled = continued.memory.recall(task['id'], child['agent_id'], {'memory_ref': memory_ref, 'section': 'arguments'}, document)
            assert json.loads(recalled['text']) == arguments
            with pytest.raises(KeyError, match='Agent and canvas'):
                continued.memory.recall(task['id'], 'main', {'memory_ref': memory_ref}, session['document'])
            assert continued.memory.search(task['id'], 'main', {'query': '私有眼睛'}, session['document'])['records'] == []
            assert reopened.get_document(document['id'])['elements'] == []
        finally:
            await continued.close()
            reopened.close()
    asyncio.run(check())


def test_search_indexes_objects_beyond_the_short_result_preview(tmp_path):
    async def check():
        store, runtime, session, task, models = workspace(tmp_path / 'index.sqlite3')
        try:
            output = await call(runtime, models, session, task, 'create_elements', {'items': [
                {'type': 'ellipse', 'name': f'节点{index}', 'x': index * 20} for index in range(24)
            ]}, 'create-many')
            document = store.get_document(session['document']['id'])
            final_id = output['changed_ids'][-1]
            found = runtime.memory.search(task['id'], 'main', {'object_ids': [final_id]}, document)
            assert [item['id'] for item in found['objects']] == [final_id]
            assert any(item['tool'] == 'create_elements' for item in found['records'])
            assert runtime.memory.recall(task['id'], 'main', {'element_id': final_id}, document)['element']['customData']['name'] == '节点23'
        finally:
            await runtime.close()
            store.close()
    asyncio.run(check())


def test_web_reference_in_memory_evidence_returns_field_error_then_valid_notes_can_be_saved(tmp_path):
    async def check():
        store, runtime, session, task, models = workspace(tmp_path / 'evidence.sqlite3')
        try:
            output = await call(runtime, models, session, task, 'compact_context', {
                'goal': '绘制人物', 'summary': '准备继续绘图',
                'evidence_refs': ['reference_10fef0495b235d6ba79c48db8d1e68d8'],
            }, 'wrong-evidence')
            assert output['ok'] is False and 'evidence_refs.0' in output['error']
            assert store.connection.execute('SELECT count(*) FROM agent_working_memory').fetchone()[0] == 0
            messages = runtime.memory.prepare_messages(task['id'], 'main', f"main:{task['id']}",
                'state', session['document'], [])
            assert any(item.get('tool_call_id') == 'wrong-evidence' and json.loads(item['content']) == output
                       for item in messages)
            saved = await call(runtime, models, session, task, 'compact_context', {
                'goal': '绘制人物', 'summary': '准备继续绘图；来源 reference_10fef0495b235d6ba79c48db8d1e68d8',
            }, 'correct-evidence')
            assert saved['ok'] and saved['revision'] == 1
            assert runtime.memory.snapshot(task['id'], 'main', f"main:{task['id']}", 'state',
                session['document'])['working_memory']['notes']['goal'] == '绘制人物'
        finally:
            await runtime.close()
            store.close()
    asyncio.run(check())


def test_path_revision_history_survives_compaction_and_excludes_other_callers(tmp_path, native_drawing_child):
    async def check():
        store, runtime, session, task, models = workspace(tmp_path / 'revisions.sqlite3')
        try:
            child = native_drawing_child(store, runtime, session, task)
            created = child.call('create_element', {
                'type': 'freedraw', 'name': '面部轮廓', 'points': [[0, 0], [30, 40], [60, 0]],
            }, 'outline')
            element_id = created['changed_ids'][0]
            for index in range(4):
                child.call('reshape_path', {
                    'element_id': element_id, 'points': [[0, 0], [30, 40 + index], [60, 0]],
                }, f'revise-{index}')
            child.call('compact_context', {
                'goal': '完成面部轮廓阶段', 'summary': '已调整轮廓，等待检查图像', 'object_ids': [element_id],
            }, 'save-stage')
            document = child.document
            snapshot = runtime.memory.snapshot(task['id'], child.agent['agent_id'], child.run['id'], 'new-state', document)
            path = snapshot['execution_history']['recent_path_edits'][0]
            assert path['element_id'] == element_id and path['revisions'] == 4
            assert path['name'] == '面部轮廓' and path['exists']
            assert next(item for item in snapshot['execution_history']['tool_counts']
                        if item['tool_name'] == 'reshape_path')['calls'] == 4
            assert snapshot['known_queries_in_current_state'] == []
            assert not runtime.memory.snapshot(task['id'], 'other-agent', child.run['id'], 'new-state',
                document)['execution_history']['tool_counts']
            assert store.get_document(session['document']['id'])['elements'] == []
            await child.submit()
            future = store.create_task(session['id'])
            continued = native_drawing_child(store, runtime, session, future, child.agent)
            continued.call('reshape_path', {
                'element_id': element_id, 'points': [[0, 0], [30, 50], [60, 0]],
            }, 'future-revision')
            assert runtime.memory.snapshot(task['id'], child.agent['agent_id'], child.run['id'], 'new-state',
                document)['execution_history']['recent_path_edits'][0]['revisions'] == 4
        finally:
            await runtime.close()
            store.close()
    asyncio.run(check())


def test_trace_range_memory_survives_compaction_and_distinguishes_renamed_duplicates(tmp_path):
    async def check():
        store, runtime, session, task, models = workspace(tmp_path / 'trace-ranges.sqlite3')
        try:
            image = Image.new('RGB', (256, 256), 'white')
            drawing = ImageDraw.Draw(image)
            drawing.line([(32, 64), (224, 64)], fill='black', width=5)
            drawing.line([(128, 160), (128, 224)], fill='black', width=5)
            data = io.BytesIO()
            image.save(data, format='PNG')
            source = runtime.references.save_image(task['id'], 'main', {
                'title': '轮廓分页记录', 'url': 'https://example.org/trace-ranges',
                'image': 'https://example.org/trace-ranges.png'}, data.getvalue(), decode_reference(data.getvalue()))
            caller = f"main:{task['id']}"
            runtime.references.view(task['id'], caller, {'reference_id': source['reference_id']})
            runtime.references.delivered(task['id'], caller, runtime.references.context(task['id'], caller)[1])
            args = {'reference_id': source['reference_id'], 'name': 'first-pass', 'mode': 'ink', 'sigma': 0,
                    'target': {'x': 0, 'y': 0, 'width': 256, 'height': 256}, 'min_length': 5, 'limit': 1}
            first = await call(runtime, models, session, task, 'trace_reference', args, 'trace-first')
            second = await call(runtime, models, session, task, 'continue_reference_trace', first['next_page'], 'trace-second')
            repeated = await call(runtime, models, session, task, 'trace_reference', {**args, 'name': 'renamed-pass'}, 'trace-renamed')
            assert first['ok'] and second['ok'] and repeated['ok']
            await call(runtime, models, session, task, 'compact_context', {
                'goal': '检查当前线条', 'summary': '已经生成轮廓，准备检查重复范围'}, 'save-trace-stage')
            document = store.get_document(session['document']['id'])
            progress = runtime.memory.snapshot(task['id'], 'main', caller, 'next-state', document)['execution_history']['reference_traces']
            assert progress['family_count'] == 1 and progress['unindexed_record_count'] == 0
            family = progress['families'][0]
            assert family['successful_calls'] == 3 and family['prepared_ranges'] == [[0, 2]]
            assert family['prepared_path_count'] == 2 and family['repeated_path_count'] == 1
            assert family['first_unprepared_offset'] is None and len(family['trace_plan_ids']) == 2
            assert set(family['names']) == {'first-pass', 'renamed-pass'}
            assert all(runtime.memory.recall(task['id'], 'main', {'memory_ref': reference}, document)['ok']
                       for reference in family['memory_refs'])
            other = runtime.memory.snapshot(task['id'], 'other-agent', caller, 'next-state', document)
            assert 'reference_traces' not in other['execution_history']
        finally:
            await runtime.close()
            store.close()
    asyncio.run(check())
