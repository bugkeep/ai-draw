import asyncio
import json

import pytest

from studio.actions import create_native_element, element_fingerprint
from studio.agents import DrawingAgents, prepare_drawing_tool
from studio.errors import validation_summary
from studio.runtime import DrawingRuntime
from studio.settings import ModelConfig, ModelSettings
from studio.store import StudioStore


def begin_input(store, runtime, session, task, ids):
    agent = store.agents.create_agent(session['id'], task['id'], f'input-{len(store.agents.list_agents(session["id"]))}',
        '修正指定对象，保持其他内容。', 'Artist', ids)
    run = runtime.children._initialize(session['id'], store.agents.next_run(session['id'], agent['agent_id'], task['id']))
    return agent, run


def native_call(store, run, name, arguments, call_id):
    encoded = json.dumps(arguments)
    store.agents.begin_call(run['owner_task_id'], run['id'], call_id, name, encoded)
    draft, selection, actions, output = prepare_drawing_tool(name, arguments,
        run['document'], run['draft'], run['selection'])
    run.update(draft=draft, selection=selection, prepared=[*run['prepared'], *actions])
    store.agents.finish_call(run['id'], call_id, output, run)
    return output


def test_missing_targets_finish_as_durable_blocked_input_without_a_model_request(tmp_path):
    async def check():
        path = tmp_path / 'missing.sqlite3'
        store = StudioStore(path)
        runtime = DrawingRuntime(store, ModelSettings(store))
        session = store.create_session()
        other = store.create_session()
        task = store.create_task(session['id'])
        protected = create_native_element({'type': 'rectangle', 'x': 20, 'y': 30})
        foreign = create_native_element({'type': 'ellipse', 'x': 80, 'y': 60})
        store.save_snapshot(session['document']['id'], 0, [protected], [])
        store.save_snapshot(other['document']['id'], 0, [foreign], [])
        try:
            agent = await runtime.children.spawn_agent(session['id'], task['id'], 'missing-targets', {
                'message': '移动指定的旧轮廓，不能改动其他对象。', 'relevant_ids': [foreign['id'], 'deleted-target']},
                ModelConfig(provider='ollama', model='llama3.1', api_base='http://localhost:11434'))
            worker = runtime.children.workers[agent['agent_id']]
            await asyncio.wait_for(worker, 5)
            final = store.agents.get_agent(session['id'], agent['agent_id'])
            completion = final['latest_result']['completion']
            assert final['state'] == 'Final' and completion['status'] == 'blocked'
            assert completion['reason'] == 'missing_edit_targets'
            assert completion['stopped_by'] == 'runtime_input_validation'
            assert set(completion['missing_ids']) == {foreign['id'], 'deleted-target'}
            assert completion['available_object_count'] == 1
            assert final['latest_result']['action_count'] == 0
            assert store.agents.pending_results(session['id']) == []
            assert not store.connection.execute('SELECT 1 FROM agent_tool_calls WHERE caller_id=?',
                (agent['run_id'],)).fetchone()
            logs = store.tool_logs(task['id'])
            assert any(item['kind'] == 'agent_input_blocked' for item in logs)
            assert not any(item['kind'] in {'model_context', 'llm_response'} for item in logs)
            assert element_fingerprint(store.get_document(session['document']['id'])['elements'][0]) == element_fingerprint(protected)
            assert store.get_document(other['document']['id'])['elements'] == [foreign]
            models = DrawingAgents(store,
                ModelConfig(provider='ollama', model='llama3.1', api_base='http://localhost:11434'))
            messages = models.main_messages(task['id'], '报告当前阻碍。',
                store.get_document(session['document']['id']), [], [])
            assert json.loads(messages[1]['content'])['child_agents'][0]['latest_result']['completion'] == completion
        finally:
            await runtime.close()
            store.close()
        reopened = StudioStore(path)
        try:
            assert reopened.agents.get_agent(session['id'], agent['agent_id'])['latest_result']['completion'] == completion
        finally:
            reopened.close()
    asyncio.run(check())


@pytest.mark.parametrize('operation', ['create', 'delete'])
def test_validation_uses_the_inherited_private_candidate_instead_of_the_shared_canvas(tmp_path, operation):
    async def check():
        store = StudioStore(tmp_path / f'{operation}.sqlite3')
        runtime = DrawingRuntime(store, ModelSettings(store))
        session = store.create_session()
        task = store.create_task(session['id'])
        element = create_native_element({'type': 'ellipse', 'x': 10, 'y': 20})
        if operation == 'delete':
            store.save_snapshot(session['document']['id'], 0, [element], [])
        try:
            agent, first = begin_input(store, runtime, session, task, [])
            if operation == 'create':
                output = native_call(store, first, 'create_element', {'type': 'ellipse'}, 'create')
                target = output['changed_ids'][0]
            else:
                target = element['id']
                native_call(store, first, 'delete_elements', {'ids': [target]}, 'delete')
            pending = store.agents.complete_run(session['id'], first['id'], '已准备原生动作')
            store.agents.enqueue_input(session['id'], task['id'], agent['agent_id'], 'continue', '继续修正该对象。', [target])
            second = runtime.children._initialize(session['id'], store.agents.next_run(session['id'], agent['agent_id'], task['id']))
            assert pending['result_id'] in second['document']['depends_on']
            blocker = runtime.children._initial_target_blocker(second)
            if operation == 'create':
                assert blocker is None
                assert not store.get_document(session['document']['id'])['elements']
            else:
                assert blocker['missing_ids'] == [target]
                assert store.get_document(session['document']['id'])['elements'] == [element]
        finally:
            await runtime.close()
            store.close()
    asyncio.run(check())


def test_partial_targets_and_resumed_deletion_are_not_rejected_by_initial_validation(tmp_path):
    async def check():
        store = StudioStore(tmp_path / 'resume.sqlite3')
        runtime = DrawingRuntime(store, ModelSettings(store))
        session = store.create_session()
        task = store.create_task(session['id'])
        element = create_native_element({'type': 'ellipse'})
        store.save_snapshot(session['document']['id'], 0, [element], [])
        try:
            _, run = begin_input(store, runtime, session, task, [element['id'], 'already-removed'])
            assert runtime.children._initial_target_blocker(run) is None
            native_call(store, run, 'delete_elements', {'ids': [element['id']]}, 'delete')
            restored = store.agents.get_run(session['id'], run['id'])
            assert not any(not item.get('isDeleted') for item in restored['draft']) and restored['prepared']
            assert runtime.children._initial_target_blocker(restored) is None
        finally:
            await runtime.close()
            store.close()
    asyncio.run(check())


def test_existing_read_records_are_preserved_for_the_normal_feedback_loop(tmp_path):
    async def check():
        store = StudioStore(tmp_path / 'read.sqlite3')
        runtime = DrawingRuntime(store, ModelSettings(store))
        session = store.create_session()
        task = store.create_task(session['id'])
        try:
            _, run = begin_input(store, runtime, session, task, ['missing'])
            assert runtime.children._initial_target_blocker(run)
            with pytest.raises(ValueError, match='do not exist') as error:
                native_call(store, run, 'inspect_scene', {'ids': ['missing']}, 'read')
            output = {'ok': False, 'error': validation_summary(error.value)}
            store.agents.finish_call(run['id'], 'read', output, run)
            assert not run['prepared']
            assert runtime.children._initial_target_blocker(store.agents.get_run(session['id'], run['id'])) is None
            assert json.loads(store.agents.recent_messages(run['id'])[0]['content']) == output
        finally:
            await runtime.close()
            store.close()
    asyncio.run(check())


def test_blocked_inputs_do_not_stop_the_remaining_input_queue(tmp_path):
    async def check():
        store = StudioStore(tmp_path / 'queue.sqlite3')
        runtime = DrawingRuntime(store, ModelSettings(store))
        session = store.create_session()
        task = store.create_task(session['id'])
        config = ModelConfig(provider='ollama', model='llama3.1', api_base='http://localhost:11434')
        try:
            first = await runtime.children.spawn_agent(session['id'], task['id'], 'first', {
                'message': '修正指定的轮廓。', 'relevant_ids': ['missing-first']}, config)
            second = await runtime.children.send_input(session['id'], task['id'], 'second', {
                'agent_id': first['agent_id'], 'message': '修正另一个轮廓。',
                'relevant_ids': ['missing-second']}, config)
            await asyncio.wait_for(runtime.children.workers[first['agent_id']], 5)
            final = store.agents.get_agent(session['id'], first['agent_id'])
            assert final['state'] == 'Final' and not final['inputs']
            for assigned, target in [(first, 'missing-first'), (second, 'missing-second')]:
                run = store.agents.get_run(session['id'], assigned['run_id'])
                assert run['status'] == 'completed'
                assert store.agents.run_summary(run)['completion']['missing_ids'] == [target]
            assert not store.connection.execute('SELECT 1 FROM agent_tool_calls WHERE task_id=?',
                (task['id'],)).fetchone()
            assert not any(row['kind'] == 'model_context' for row in store.tool_logs(task['id']))
        finally:
            await runtime.close()
            store.close()
    asyncio.run(check())
