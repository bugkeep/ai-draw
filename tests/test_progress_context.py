import copy
import hashlib
import json
from pathlib import Path

from studio.agents import main_replan_ids
from studio.progress_context import agent_progress, input_progress, result_progress, PROGRESS_RETRIEVAL


def test_recorded_main_progress_keeps_current_findings_identity_and_retrieval_without_repeated_reports():
    fixture = json.loads((Path(__file__).parent / 'fixtures/main-progress-context.json').read_text(encoding='utf-8'))
    prompt = fixture['prompt']
    assert hashlib.sha256(json.dumps(prompt, ensure_ascii=False).encode()).hexdigest() == fixture['original_prompt_sha256']
    original = copy.deepcopy(prompt)
    progress = prompt['task_progress']
    projected = {**prompt,
        'child_agents': [agent_progress(agent) for agent in prompt['child_agents']],
        'task_progress': {**progress, 'inputs': [input_progress(run) for run in progress['inputs']],
            'submitted_results': [result_progress(result) for result in progress['submitted_results']],
            'rejected_results': [result_progress(result) for result in progress['rejected_results']]},
        'pending_results': [result_progress(result) for result in prompt['pending_results']],
        'progress_details': PROGRESS_RETRIEVAL}
    for before, after in zip(prompt['child_agents'], projected['child_agents']):
        assert {key: after[key] for key in ('agent_id', 'state', 'revision', 'inputs')} == {
            key: before[key] for key in ('agent_id', 'state', 'revision', 'inputs')}
        assert after['latest_result']['completion'] == before['latest_result']['completion']
        assert after['latest_result']['error'] == before['latest_result']['error']
        assert 'pending_results' not in after and 'summary' not in after
    assert projected['task_progress']['committed_actions'] == progress['committed_actions']
    messages = lambda value: [{'role': 'system', 'content': ''}, {'role': 'user', 'content': json.dumps(value)}]
    assert main_replan_ids(messages(projected)) == main_replan_ids(messages(prompt))
    assert prompt == original
    assert len(json.dumps(projected, ensure_ascii=False)) < len(json.dumps(prompt, ensure_ascii=False))


def test_non_visual_results_keep_their_complete_summary_and_error():
    fixture = json.loads((Path(__file__).parent / 'fixtures/main-progress-context.json').read_text(encoding='utf-8'))
    agent = copy.deepcopy(fixture['prompt']['child_agents'][0])
    # Exercise the saved-record shape without a visual assessment; no model reply is generated here.
    agent['latest_result'].pop('completion')
    expected = copy.deepcopy(agent['latest_result'])
    assert agent_progress(agent)['latest_result'] == expected
    assert input_progress(expected)['error'] == expected['error']
