import asyncio
import json

from studio.agents import DrawingAgents
from studio.runtime import DrawingRuntime
from studio.settings import ModelConfig, ModelSettings
from studio.store import StudioStore


def test_main_context_scopes_results_to_current_task_and_keeps_reusable_agent_ids(tmp_path):
    async def check():
        store = StudioStore(tmp_path / 'task-context.sqlite3')
        runtime = DrawingRuntime(store, ModelSettings(store))
        session = store.create_session()
        foreign = store.create_session()
        old_task = store.create_task(session['id'])
        foreign_task = store.create_task(foreign['id'])
        config = ModelConfig(provider='ollama', model='llama3.1', api_base='http://localhost:11434')

        async def assigned(session_id, task_id, call_id, target):
            agent = await runtime.children.spawn_agent(session_id, task_id, call_id, {
                'message': '修正指定对象。', 'relevant_ids': [target]}, config)
            await asyncio.wait_for(runtime.children.workers[agent['agent_id']], 5)
            return agent

        try:
            old = await assigned(session['id'], old_task['id'], 'old', 'old-missing-target')
            other = await assigned(foreign['id'], foreign_task['id'], 'foreign', 'foreign-missing-target')
            current_task = store.create_task(session['id'])
            current = await assigned(session['id'], current_task['id'], 'current', 'current-missing-target')
            models = DrawingAgents(store, config)

            def context():
                return json.loads(models.main_messages(current_task['id'], '报告当前任务的阻碍。',
                    store.get_document(session['document']['id']), [], [])[1]['content'])

            prompt = context()
            assert [agent['agent_id'] for agent in prompt['child_agents']] == [current['agent_id']]
            assert prompt['child_agents'][0]['latest_result']['completion']['missing_ids'] == ['current-missing-target']
            assert prompt['available_child_agents'] == [{
                'agent_id': old['agent_id'], 'nickname': old['nickname'], 'state': 'Final'}]
            encoded = json.dumps(prompt)
            assert 'old-missing-target' not in encoded and old['run_id'] not in encoded
            assert other['agent_id'] not in encoded and 'foreign-missing-target' not in encoded
            reused = await runtime.children.send_input(session['id'], current_task['id'], 'reuse', {
                'agent_id': old['agent_id'], 'message': '修正本轮指定对象。',
                'relevant_ids': ['reused-current-target']}, config)
            await asyncio.wait_for(runtime.children.workers[old['agent_id']], 5)
            prompt = context()
            assert not prompt['available_child_agents']
            active = next(agent for agent in prompt['child_agents'] if agent['agent_id'] == old['agent_id'])
            assert active['latest_result']['run_id'] == reused['run_id']
            assert active['latest_result']['completion']['missing_ids'] == ['reused-current-target']
            assert 'old-missing-target' not in json.dumps(prompt)
        finally:
            await runtime.close()
            store.close()
    asyncio.run(check())
