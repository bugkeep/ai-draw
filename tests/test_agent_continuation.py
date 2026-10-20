import asyncio
import json
from pathlib import Path

import pytest

from studio.agent_tools import validate_tool
from studio.agents import DrawingAgents, model_output_feedback, prepare_drawing_tool
from studio.runtime import DrawingRuntime
from studio.settings import ModelConfig, ModelSettings
from studio.store import StudioStore


RECORDED = json.loads((Path(__file__).parent / 'fixtures' / 'recorded_truncated_child_response.json').read_text(encoding='utf-8'))


def workspace(tmp_path):
    store = StudioStore(tmp_path / 'continuation.sqlite3')
    runtime = DrawingRuntime(store, ModelSettings(store))
    session = store.create_session('截断响应回放')
    task = store.create_task(session['id'])
    agent = store.agents.create_agent(session['id'], task['id'], 'recorded-landscape',
        RECORDED['assignment'], 'Landscape', [])
    run = runtime.children._initialize(session['id'], store.agents.next_run(session['id'], agent['agent_id'], task['id']))
    assert run['draft'] == RECORDED['initial_document']['elements'] == []
    return store, runtime, session, task, agent, run


def save_recorded_response(store, run):
    payload = RECORDED['response']
    assert payload['finish_reason'] == 'length' and payload['usage']['completion_tokens'] == 4096
    store.log_tool(run['owner_task_id'], run['agent_id'], 1, 'llm_response', payload)
    store.agents.save_response(run, payload['message'])
    return model_output_feedback(store, run['owner_task_id'], run['agent_id'], payload['message'], run['id'])


def test_recorded_cut_batch_preserves_valid_actions_and_main_continues_same_input(tmp_path):
    async def check():
        store, runtime, session, task, agent, run = workspace(tmp_path)
        models = DrawingAgents(store, ModelConfig(provider='ollama', model='llama3.1', api_base='http://localhost:11434'))
        save_recorded_response(store, run)
        worker = asyncio.create_task(runtime.children._execute_run(models, session['id'], run))
        runtime.children.workers[agent['agent_id']] = worker
        try:
            observed = await runtime.children.wait_agent(session['id'], [agent['agent_id']])
            event, = observed['continuations']
            interrupted = store.agents.get_run(session['id'], run['id'])
            assert observed['agents'][0]['state'] == 'Running' and observed['results'] == []
            assert event['run_id'] == run['id'] and event['input_id'] == run['input_id']
            assert interrupted['pending_response'] is None and interrupted['result_id'] is None
            expected_paths = sum(len(json.loads(call['function']['arguments'])['paths'])
                for call in RECORDED['response']['message']['tool_calls'][:-1])
            assert len(interrupted['prepared']) == len(interrupted['draft']) == expected_paths
            calls = store.connection.execute('SELECT call_id,output FROM agent_tool_calls WHERE caller_id=? ORDER BY rowid',
                (run['id'],)).fetchall()
            assert len(calls) == 6 and all(json.loads(row['output'])['ok'] for row in calls[:-1])
            assert json.loads(calls[-1]['output'])['ok'] is False
            errors = [json.loads(message['content']) for message in interrupted['conversation'] if message['role'] == 'tool']
            raw_error = json.loads(calls[-1]['output'])
            assert {key: errors[-1][key] for key in raw_error} == raw_error
            assert errors[-1]['memory_ref'].startswith('memory_')
            prompt = json.loads(models.main_messages(task['id'], '继续绘图',
                store.get_document(session['document']['id']), [], [])[1]['content'])
            assert prompt['child_agents'][0]['continuations'] == observed['continuations']
            assert store.get_document(session['document']['id'])['elements'] == []
            with pytest.raises(ValueError, match='continued'):
                store.agents.complete_run(session['id'], run['id'], '截断时不能完成')
            again = await runtime.children.wait_agent(session['id'], [agent['agent_id']], observed['revisions'])
            assert again['continuations'] == observed['continuations']
            tool = {'id': 'main-continue', 'type': 'function', 'function': {'name': 'continue_agent',
                'arguments': json.dumps({'agent_id': agent['agent_id'], 'continuation_id': event['continuation_id'],
                    'message': '继续完成剩余松针，保留已经准备的远山、溪流、桥梁和树干。'}, ensure_ascii=False)}}
            continued = await runtime.call_main_tool(models, task['id'], session['id'], session['document']['id'], tool)
            assert continued['ok'] and not continued['already_continued']
            assert continued['input_id'] == run['input_id'] and continued['run_id'] == run['id']
            cached = await runtime.call_main_tool(models, task['id'], session['id'], session['document']['id'], tool)
            assert cached == continued
            assert store.agents.pending_continuations(session['id'], agent['agent_id']) == []
            saved = store.agents.get_run(session['id'], run['id'])
            signal = json.loads(saved['conversation'][-1]['content'])
            assert signal['signal'] == 'continue' and signal['continuation_id'] == event['continuation_id']
            assert saved['draft'] == interrupted['draft'] and saved['prepared'] == interrupted['prepared']
            assert store.connection.execute('SELECT count(*) FROM agent_inputs').fetchone()[0] == 1
            assert len([item for item in store.tool_logs(task['id']) if item['kind'] == 'llm_response']) == 1
        finally:
            await runtime.children.close_agent(session['id'], agent['agent_id'])
            store.close()
    asyncio.run(check())


def test_restart_mid_truncated_batch_keeps_original_response_and_tool_receipts(tmp_path):
    async def check():
        store, runtime, session, task, agent, run = workspace(tmp_path)
        save_recorded_response(store, run)
        call = RECORDED['response']['message']['tool_calls'][0]
        name, raw_args = call['function']['name'], call['function']['arguments']
        store.agents.begin_call(task['id'], run['id'], call['id'], name, raw_args)
        draft, selection, prepared, output = prepare_drawing_tool(
            name, json.loads(raw_args), run['document'], run['draft'], run['selection'])
        run.update(draft=draft, selection=selection, prepared=prepared)
        store.agents.finish_call(run['id'], call['id'], output, run)
        original_ids = set(output['changed_ids'])
        path = store.database_path
        store.close()
        reopened = StudioStore(path)
        restarted = DrawingRuntime(reopened, ModelSettings(reopened))
        next_task = reopened.create_task(session['id'])
        reopened.agents.resume_agent(session['id'], next_task['id'], agent['agent_id'])
        restored = reopened.agents.next_run(session['id'], agent['agent_id'], next_task['id'])
        models = DrawingAgents(reopened, ModelConfig(provider='ollama', model='llama3.1', api_base='http://localhost:11434'))
        worker = asyncio.create_task(restarted.children._execute_run(models, session['id'], restored))
        restarted.children.workers[agent['agent_id']] = worker
        try:
            observed = await restarted.children.wait_agent(session['id'], [agent['agent_id']])
            event, = observed['continuations']
            current = reopened.agents.get_run(session['id'], run['id'])
            assert current['owner_task_id'] == next_task['id'] and current['id'] == run['id']
            assert event['task_id'] == next_task['id'] and current['input_id'] == run['input_id']
            assert original_ids <= {item['id'] for item in current['draft']}
            expected = sum(len(json.loads(item['function']['arguments'])['paths'])
                for item in RECORDED['response']['message']['tool_calls'][:-1])
            assert len(current['prepared']) == len(current['draft']) == expected
            cached = [item for item in reopened.tool_logs(next_task['id']) if item['kind'] == 'function_call_output'
                and item['payload']['call_id'] == call['id']]
            assert len(cached) == 1 and cached[0]['payload']['cached']
            assert len([item for item in reopened.tool_logs(task['id']) if item['kind'] == 'llm_response']) == 1
        finally:
            await restarted.children.close_agent(session['id'], agent['agent_id'])
            reopened.close()
    asyncio.run(check())


def test_continuation_survives_restart_and_does_not_dequeue_next_input(tmp_path):
    store, runtime, session, task, agent, run = workspace(tmp_path)
    feedback = save_recorded_response(store, run)
    event = store.agents.suspend_for_continuation(session['id'], run['id'],
        [RECORDED['response']['message'], {'role': 'user', 'content': json.dumps(feedback)}], feedback)
    queued = store.agents.enqueue_input(session['id'], task['id'], agent['agent_id'], 'later-pass', '下一阶段', [])
    before = store.agents.get_run(session['id'], run['id'])
    path = store.database_path
    store.close()
    reopened = StudioStore(path)
    restarted = DrawingRuntime(reopened, ModelSettings(reopened))
    try:
        assert reopened.agents.get_agent(session['id'], agent['agent_id'])['state'] == 'Shutdown'
        assert reopened.agents.pending_continuations(session['id'], agent['agent_id'])[0]['continuation_id'] == event['continuation_id']
        with pytest.raises(ValueError, match='Shutdown'):
            reopened.agents.continue_agent(session['id'], task['id'], agent['agent_id'], event['continuation_id'])
        next_task = reopened.create_task(session['id'])
        reopened.agents.resume_agent(session['id'], next_task['id'], agent['agent_id'])
        continued = reopened.agents.continue_agent(session['id'], next_task['id'], agent['agent_id'], event['continuation_id'])
        picked = reopened.agents.next_run(session['id'], agent['agent_id'], next_task['id'])
        assert continued['run_id'] == picked['id'] == run['id']
        assert picked['draft'] == before['draft'] and picked['prepared'] == before['prepared']
        assert reopened.agents.get_run(session['id'], queued['run_id'])['status'] == 'queued'
        duplicate = reopened.agents.continue_agent(session['id'], next_task['id'], agent['agent_id'], event['continuation_id'])
        assert duplicate['already_continued']
        assert reopened.agents.get_run(session['id'], run['id'])['conversation'] == picked['conversation']
        # 原始返回再次用于确定性回放，验证旧信号不会确认新中断。
        newer_feedback = save_recorded_response(reopened, picked)
        newer = reopened.agents.suspend_for_continuation(session['id'], picked['id'], picked['conversation'], newer_feedback)
        assert newer['continuation_id'] != event['continuation_id']
        reopened.agents.continue_agent(session['id'], next_task['id'], agent['agent_id'], event['continuation_id'])
        assert reopened.agents.pending_continuations(session['id'], agent['agent_id'])[0]['continuation_id'] == newer['continuation_id']
        assert restarted.children.workers == {}
    finally:
        reopened.close()


def test_continuation_checks_agent_canvas_task_and_child_permissions(tmp_path):
    store, runtime, session, task, agent, run = workspace(tmp_path)
    try:
        feedback = save_recorded_response(store, run)
        event = store.agents.suspend_for_continuation(session['id'], run['id'], [], feedback)
        other = store.create_session('独立画布')
        other_task = store.create_task(other['id'])
        another = store.agents.create_agent(session['id'], task['id'], 'other-child', '其他工作', 'Other', [])
        with pytest.raises(KeyError, match='canvas'):
            store.agents.continue_agent(other['id'], other_task['id'], agent['agent_id'], event['continuation_id'])
        with pytest.raises(KeyError, match='Continuation'):
            store.agents.continue_agent(session['id'], task['id'], another['agent_id'], event['continuation_id'])
        with pytest.raises(KeyError, match='Task'):
            store.agents.continue_agent(session['id'], other_task['id'], agent['agent_id'], event['continuation_id'])
        later_task = store.create_task(session['id'])
        with pytest.raises(ValueError, match='current task'):
            store.agents.continue_agent(session['id'], later_task['id'], agent['agent_id'], event['continuation_id'])
        with pytest.raises(ValueError, match='cannot'):
            validate_tool('continue_agent', {'agent_id': agent['agent_id'], 'continuation_id': event['continuation_id']}, child=True)
        assert store.agents.get_run(session['id'], run['id'])['status'] == 'running'
        assert store.get_session(other['id'])['document'] == other['document']
        assert len(store.agents.pending_continuations(session['id'], agent['agent_id'])) == 1
        store.agents.pause_agent(session['id'], agent['agent_id'])
        store.delete_session(session['id'])
        assert store.connection.execute('SELECT count(*) FROM agent_continuations').fetchone()[0] == 0
    finally:
        store.close()
