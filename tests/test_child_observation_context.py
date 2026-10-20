import asyncio
import copy
import json
from collections import Counter
from pathlib import Path

import pytest

from studio.agents import DrawingAgents
from studio.child_harness import observation_context
from studio.runtime import DrawingRuntime
from studio.settings import ModelConfig, ModelSettings
from studio.store import StudioStore


def test_recorded_truncated_batch_keeps_actual_errors_and_images_with_observation_prefix(tmp_path):
    async def check():
        recorded = json.loads((Path(__file__).parent / 'fixtures' / 'recorded_truncated_child_response.json').read_text(encoding='utf-8'))
        store = StudioStore(tmp_path / 'observations.sqlite3')
        runtime = DrawingRuntime(store, ModelSettings(store))
        session = store.create_session('实际截断记录的图片与错误消息顺序')
        task = store.create_task(session['id'])
        actor = store.agents.create_agent(session['id'], task['id'], 'recorded-observation-order',
            recorded['assignment'], 'Recorded', [])
        run = runtime.children._initialize(session['id'], store.agents.next_run(session['id'], actor['agent_id'], task['id']))
        models = DrawingAgents(store, ModelConfig(provider='ollama', model='llama3.1', api_base='http://localhost:11434'))
        store.log_tool(task['id'], actor['agent_id'], 1, 'llm_response', recorded['response'])
        store.agents.save_response(run, recorded['response']['message'])
        worker = asyncio.create_task(runtime.children._execute_run(models, session['id'], run))
        runtime.children.workers[actor['agent_id']] = worker
        try:
            observed = await runtime.children.wait_agent(session['id'], [actor['agent_id']])
            assert len(observed['continuations']) == 1
            run = store.agents.get_run(session['id'], run['id'])
            document = {**run['document'], 'elements': run['draft'], 'selection': run['selection']}
            before_document = copy.deepcopy(document)
            pictures, image_ids = await runtime.vision.context(task['id'], actor['agent_id'], run['id'], document)
            assert pictures and image_ids
            agent = store.agents.get_agent(session['id'], actor['agent_id'])
            prepared = runtime.memory.prepare_messages(task['id'], actor['agent_id'], run['id'],
                f"{run['id']}:{len(run['prepared'])}", document, models.child_messages(run, agent))
            original = copy.deepcopy(prepared)
            before = [*prepared, *pictures]
            after = observation_context(prepared, [], pictures)
            serialize = lambda message: json.dumps(message, sort_keys=True, ensure_ascii=False)
            assert Counter(map(serialize, before)) == Counter(map(serialize, after))
            assert prepared == original and document == before_document
            actual_errors = [json.loads(row['output']) for row in store.connection.execute(
                'SELECT output FROM agent_tool_calls WHERE caller_id=? AND json_extract(output,\'$.ok\')=0', (run['id'],))]
            assert actual_errors
            delivered = [json.loads(message['content']) for message in after if message.get('role') == 'tool'
                and json.loads(message['content']).get('ok') is False]
            for error in actual_errors:
                assert any(all(item.get(key) == value for key, value in error.items()) for item in delivered)
            for index, message in enumerate(after):
                if message.get('role') != 'assistant' or not message.get('tool_calls'):
                    continue
                ids = {call['id'] for call in message['tool_calls']}
                end = index + 1
                returned = []
                while end < len(after) and after[end].get('role') == 'tool':
                    returned.append(after[end]['tool_call_id'])
                    end += 1
                assert set(returned) == ids and len(returned) == len(ids)
            assert store.get_document(session['document']['id'])['elements'] == []
        finally:
            await runtime.children.close_agent(session['id'], actor['agent_id'])
            await runtime.close()
            store.close()
    asyncio.run(check())


def test_observations_require_the_actual_child_prefix():
    with pytest.raises(ValueError, match='current assignment prefix'):
        observation_context([], [], [])
