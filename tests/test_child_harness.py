import json
from pathlib import Path

import pytest

from studio.child_harness import repair_context, report_context
from studio.draft_review import DraftRevision, DraftReview
from studio.agents import truncation_feedback
from studio.drawing_dsl import assignment, parse_report


def test_recorded_ineffective_correction_retains_review_and_allows_continued_drawing():
    recorded = json.loads((Path(__file__).parent / 'fixtures' / 'recorded_ineffective_draft.json').read_text(encoding='utf-8'))
    review = recorded['review']
    assert DraftRevision.model_validate(review['assessment']['revision']).change == 'ineffective'
    message = repair_context(DraftReview, review, None, recorded['replan'])
    feedback = json.loads(message['content'])
    assert feedback['current_private_draft_review'] == review
    assert feedback['repair_strategy']['next_strategy'] == recorded['replan']['next_strategy']
    assert feedback['repair_strategy']['review_key'] == review['review_key']
    assert 'Tools remain available' in feedback['repair_strategy']['instruction']
    assert 'NO tool calls' not in message['content']
    assert 'main_replan_request' not in feedback


def test_recorded_provider_truncation_requires_continuation_with_saved_composition():
    recorded = json.loads((Path(__file__).parent / 'fixtures' / 'recorded_model_output_limits.json').read_text(encoding='utf-8'))
    valid, *cut = recorded['responses']
    assert truncation_feedback(valid['finish_reason'], valid['usage']) is None
    for response in cut:
        feedback = truncation_feedback(response['finish_reason'], response['usage'], recorded['draft_call_id'])
        assert feedback['ok'] is False and feedback['error_kind'] == 'model_output_truncated'
        assert feedback['completion_tokens'] == 4096
        assert feedback['drawspec_draft_call_id'] == recorded['draft_call_id']
        assert 'smaller COMPLETE tool calls' in feedback['instruction']


def test_report_guide_lists_exact_allowed_ids_for_recorded_failures():
    recorded = json.loads((Path(__file__).parent / 'fixtures' / 'recorded_report_failures.json').read_text(encoding='utf-8'))
    spec = assignment(recorded['assignment']).drawspec
    guide = report_context(spec)
    allowed = {key for group in guide['allowed_constraint_ids'].values() for key in group}
    assert allowed == {item.id for item in [*spec.composition.proportions, *spec.composition.relations, *spec.composition.stages]}
    assert allowed.isdisjoint({item.id for item in spec.composition.value_groups})
    assert guide['required_form_ids'] == [item.id for item in spec.composition.forms]
    assert 'double-quoted' in guide['instruction'] and 'checks may be empty' in guide['instruction']
    provenance = recorded['provenance']
    run = {'id': provenance['run_id'], 'input_id': provenance['input_id'], 'agent_id': provenance['agent_id'],
        'message': recorded['assignment'], 'draft': recorded['native_elements']}
    for candidate in recorded['reports'][:-1]:
        content = candidate['response']['message']['content']
        with pytest.raises(ValueError):
            parse_report(content, run)
        if candidate['response_log_id'] != 129:
            report = json.loads(content)
            assert {check['constraint_id'] for check in report['checks']} - allowed
    content = recorded['reports'][-1]['response']['message']['content']
    summary, report = parse_report(content, run)
    assert summary and report['checks_are_artist_claims']
    assert {check['constraint_id'] for check in report['checks']} <= allowed
