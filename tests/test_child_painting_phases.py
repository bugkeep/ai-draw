import copy
import json
from pathlib import Path

from studio.child_art_context import artistic_brief
from studio.child_execution import execution_context
from studio.drawing_dsl import DrawingAssignment


def recorded():
    fixture = json.loads((Path(__file__).parent / 'fixtures/recorded_report_failures.json').read_text(encoding='utf-8'))
    return DrawingAssignment.model_validate_json(fixture['assignment']), fixture['native_elements']


def test_declared_later_phases_have_the_same_method_as_their_own_checkpoint():
    request, native = recorded()
    spec = request.drawspec.model_dump(mode='json')
    messages = [{'role': 'system', 'content': 'Drawing child'},
        {'role': 'user', 'content': json.dumps({'goal': request.goal, 'drawspec': spec})}]
    original = copy.deepcopy(messages)
    projected = execution_context({'draft': native}, messages)
    prompt = json.loads(projected[1]['content'])
    brief = prompt['art_execution']
    assert messages == original and prompt['goal'] == request.goal and prompt['drawspec'] == spec
    stages = spec['composition']['stages']
    position = next(index for index, item in enumerate(stages) if item['id'] == spec['stage_id'])
    assert brief['stage_id'] == spec['stage_id']
    assert [item['id'] for item in brief['later_planned_stages']] == [item['id'] for item in stages[position + 1:]]
    for item, stage in zip(brief['later_planned_stages'], stages[position + 1:]):
        next_spec = {**copy.deepcopy(spec), 'stage_id': stage['id']}
        next_request = DrawingAssignment.model_validate({**request.model_dump(), 'drawspec': next_spec})
        own_checkpoint = artistic_brief({'goal': next_request.goal,
            'drawspec': next_request.drawspec.model_dump(mode='json')})
        assert item['method'] == own_checkpoint['painting_sequence']
        assert item['criteria'] == stage['criteria']
        assert set(item['assigned_form_ids']) <= set(brief['assigned_form_ids'])
    assert request.drawspec.model_dump(mode='json') == spec


def test_later_method_guidance_retains_explicit_form_scope_and_original_stage_criteria():
    request, native = recorded()
    data = request.model_dump(mode='json')
    stages = data['drawspec']['composition']['stages']
    forms = data['drawspec']['composition']['forms']
    selected, outside = forms[0]['id'], forms[-1]['id']
    data['goal'] = '本次仅建立指定形体的 block-in，保留其余形体，后续阶段另行安排。'
    data['drawspec']['intent'] = 'repair'
    data['drawspec']['form_ids'] = [selected]
    stages[-1]['form_ids'] = [outside]
    scoped = DrawingAssignment.model_validate(data)
    spec = scoped.drawspec.model_dump(mode='json')
    messages = [{'role': 'system', 'content': 'Drawing child'},
        {'role': 'user', 'content': json.dumps({'goal': scoped.goal, 'drawspec': spec})}]
    original = copy.deepcopy(messages)
    prompt = json.loads(execution_context({'draft': native}, messages)[1]['content'])
    brief = prompt['art_execution']
    assert messages == original and prompt['goal'] == scoped.goal and prompt['drawspec'] == spec
    assert brief['assigned_form_ids'] == [selected]
    assert brief['stage_id'] == scoped.drawspec.stage_id
    last = brief['later_planned_stages'][-1]
    assert last['id'] == stages[-1]['id'] and last['assigned_form_ids'] == []
    assert last['criteria'] == stages[-1]['criteria'] and last['method']
    for row in brief['later_planned_stages']:
        assert outside not in row['assigned_form_ids']
    refs = brief['original_fields']
    for key, expected in (('stage_criteria', stages[0]['criteria']),
                          ('success_criteria', spec['success_criteria'])):
        value = prompt
        for part in refs.get(key, []):
            value = value[part]
        assert (value if key in refs else brief[key]) == expected


def test_structural_completion_guidance_is_limited_to_the_original_block_in_execution():
    request, _ = recorded()
    spec = request.drawspec.model_dump(mode='json')
    original = copy.deepcopy(spec)
    assert artistic_brief({'drawspec': spec})['block_in_completion']
    assert spec == original
    for intent in ('repair', 'edit', 'research'):
        brief = artistic_brief({'drawspec': {**spec, 'intent': intent}})
        assert brief is None or 'block_in_completion' not in brief
    for stage in spec['composition']['stages'][1:]:
        later_spec = {**spec, 'stage_id': stage['id']}
        brief = artistic_brief({'drawspec': later_spec})
        assert 'block_in_completion' not in brief and brief['stage_id'] == stage['id']
