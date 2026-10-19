import json

import pytest

from studio.agents import prepare_drawing_tool
from studio.errors import validation_summary
from studio.memory import MEMORY_TOOLS


class NativeDrawingChild:
    """Arrange real private native actions and SQLite records without generating a model reply."""

    def __init__(self, store, runtime, session, task, agent=None):
        self.store, self.runtime, self.session, self.task = store, runtime, session, task
        records = store.agents
        if agent is None:
            self.agent = records.create_agent(session['id'], task['id'], 'native-fixture',
                '准备本次原生笔画', 'Artist', [])
        else:
            records.enqueue_input(session['id'], task['id'], agent['agent_id'],
                f"input-{task['id']}", '继续修改原对象', [])
            self.agent = records.get_agent(session['id'], agent['agent_id'])
        self.run = runtime.children._initialize(session['id'],
            records.next_run(session['id'], self.agent['agent_id'], task['id']))

    @property
    def document(self):
        return {**self.run['document'], 'elements': self.run['draft'], 'selection': self.run['selection']}

    def call(self, name, arguments, call_id):
        run, records = self.run, self.store.agents
        encoded = json.dumps(arguments)
        cached = records.begin_call(self.task['id'], run['id'], call_id, name, encoded)
        if cached is not None:
            return cached
        try:
            if name in MEMORY_TOOLS:
                output = self.runtime.memory.call(self.task['id'], self.agent['agent_id'], name, arguments, self.document)
            else:
                draft, selected, actions, output = prepare_drawing_tool(
                    name, arguments, run['document'], run['draft'], run['selection'])
                run.update(draft=draft, selection=selected, prepared=[*run['prepared'], *actions])
        except (ValueError, KeyError, TypeError) as error:
            output = {'ok': False, 'error': validation_summary(error)}
        records.finish_call(run['id'], call_id, output, run)
        self.runtime.memory.for_context(self.task['id'], self.agent['agent_id'], run['id'], call_id,
            name, encoded, output, f"{run['id']}:{len(run['prepared'])}")
        return output

    async def submit(self):
        result = self.store.agents.complete_run(self.session['id'], self.run['id'], '原生动作已准备')
        return await self.runtime.apply_result(self.task['id'], self.session['id'],
            self.session['document']['id'], result['result_id'])


@pytest.fixture
def native_drawing_child():
    return NativeDrawingChild
