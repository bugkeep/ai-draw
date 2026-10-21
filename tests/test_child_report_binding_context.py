import copy
import json
from pathlib import Path

import pytest

from studio.actions import apply_action, create_native_element
from studio.child_context_projection import rejected_report_memories, report_binding_repair
from studio.child_execution import execution_context
from studio.drawing_dsl import assignment, parse_report


def recorded_deletion():
    fixture = json.loads((Path(__file__).parent / 'fixtures/recorded_report_failures.json').read_text(encoding='utf-8'))
    content = fixture['reports'][-1]['response']['message']['content']
    report = json.loads(content)
    removed = report['bindings'][0]['object_ids'][0]
    draft, _, _ = apply_action(fixture['native_elements'], [], {'kind': 'delete_elements', 'args': {'ids': [removed]}})
    provenance = fixture['provenance']
    run = {'id': provenance['run_id'], 'agent_id': provenance['agent_id'], 'input_id': provenance['input_id'],
        'message': fixture['assignment'], 'draft': draft}
    with pytest.raises(ValueError, match='Report object_ids must exist') as failed:
        parse_report(content, run)
    error = {'ok': False, 'error_kind': 'drawing_protocol', 'error': str(failed.value)}
    messages = [{'role': 'system', 'content': 'Drawing child'},
        {'role': 'user', 'content': json.dumps({'goal': assignment(fixture['assignment']).goal,
            'drawspec': assignment(fixture['assignment']).drawspec.model_dump(mode='json')})},
        {'role': 'assistant', 'content': content}, {'role': 'user', 'content': json.dumps(error)}]
    return run, messages, report, removed


def index_for(run):
    return {'objects': [{'id': item['id'], 'name': item.get('customData', {}).get('name', '')}
        for item in run['draft'] if not item.get('isDeleted')]}


def test_actual_deleted_report_reference_is_localized_without_changing_the_report_or_error():
    run, messages, report, removed = recorded_deletion()
    index = index_for(run)
    before = copy.deepcopy((run, messages, index))
    repair = report_binding_repair(run, messages, index)
    assert (run, messages, index) == before
    assert repair['run_id'] == run['id']
    assert repair['invalid_bindings'] == [{'form_id': report['bindings'][0]['form_id'],
        'unavailable_reference_count': 1,
        'still_live_binding_ids': [key for key in report['bindings'][0]['object_ids'] if key != removed],
        'current_name_matches': []}]
    messages.append({'role': 'user', 'content': json.dumps({'drawing_execution_memory': {'current_object_index': index}})})
    original = copy.deepcopy(messages)
    projected = execution_context(run, messages)
    journal = json.loads(projected[-1]['content'])['drawing_execution_memory']
    assert journal['report_binding_repair'] == repair
    assert list(journal)[-1] == 'report_binding_repair'
    assert messages == original and projected[3:-1] == messages[3:-1]
    memory = json.loads(projected[2]['content'])['rejected_drawing_report_memory']
    assert memory['summary'] == report['summary'] and memory['unresolved'] == report['unresolved']
    assert execution_context(run, projected) == projected
    with pytest.raises(ValueError, match='Report object_ids must exist'):
        parse_report(messages[2]['content'], run)


def test_name_matches_use_only_current_owned_objects_and_only_after_a_real_rejection():
    run, messages, report, removed = recorded_deletion()
    form = report['bindings'][0]['form_id']
    native = create_native_element({'type': 'freedraw', 'name': form + '/rebuilt contour',
        'pointSpace': 'absolute', 'points': [[0, 10], [30, 0], [60, 10]]})
    foreign = create_native_element({'type': 'freedraw', 'name': form + '/other document',
        'pointSpace': 'absolute', 'points': [[0, 10], [30, 0], [60, 10]]})
    run['draft'].append(native)
    index = index_for(run)
    index['objects'].append({'id': foreign['id'], 'name': foreign['customData']['name']})
    before = copy.deepcopy((run, messages, index))
    repair = report_binding_repair(run, messages, index)
    assert repair['invalid_bindings'][0]['unavailable_reference_count'] == 1
    assert repair['invalid_bindings'][0]['current_name_matches'] == [
        {'id': native['id'], 'name': native['customData']['name'], 'binding_candidate': {
            'form_id': form, 'object_ids': [
                *repair['invalid_bindings'][0]['still_live_binding_ids'], native['id']]}}]
    assert (run, messages, index) == before
    assert report_binding_repair(run, messages[:-1], index) is None
    assert report_binding_repair(run, [*messages, messages[2]], index) is None
    with pytest.raises(ValueError, match='Report object_ids must exist'):
        parse_report(messages[2]['content'], run)


def test_actual_repeated_rejected_reports_keep_latest_feedback_and_compact_historical_claims():
    fixture = json.loads((Path(__file__).parent / 'fixtures/recorded_binding_retry_context.json').read_text(encoding='utf-8'))
    messages = fixture['messages']
    run = {'id': fixture['run_id'], 'draft': fixture['native_identities']}
    before = copy.deepcopy((run, messages))
    projected = rejected_report_memories(run, messages)
    assert (run, messages) == before
    assert projected[-1] == messages[-1]
    latest = json.loads(projected[-2]['content'])['rejected_drawing_report_memory']
    assert latest['checks'] == json.loads(messages[-2]['content'])['checks']
    assert latest['reference_bindings'][0]['form_id'] == json.loads(messages[-2]['content'])['bindings'][0]['form_id']
    assert rejected_report_memories(run, projected) == projected
    assert 'fe8f5b4b73492d' not in json.dumps(projected)
    for position in (0, 2):
        report = json.loads(messages[position]['content'])
        memory = json.loads(projected[position]['content'])['rejected_drawing_report_memory']
        assert memory['summary'] == report['summary']
        assert memory['unresolved'] == report['unresolved']
        assert memory['unresolved_checks'] == [check for check in report['checks'] if check['status'] == 'unresolved']
        assert memory['reported_form_ids'] == [binding['form_id'] for binding in report['bindings']]
        assert memory['unavailable_reference_count'] == 1 and memory['status'] == 'rejected_report_claims'
        assert 'fe8f5b4b73492d' not in projected[position]['content']
        assert projected[position + 1] == messages[position + 1]
    assert sum(len(item['content']) for item in projected) < sum(len(item['content']) for item in messages)
    index = index_for(run)
    repair = report_binding_repair(run, projected, index)
    assert repair['invalid_bindings'][0]['current_name_matches'][0]['id'] == 'fe8f5b5b4b73492d'
