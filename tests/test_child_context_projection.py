import asyncio
import copy
import json
from pathlib import Path

from shapely import LineString

from studio.agents import DrawingAgents
from studio.child_context_projection import compact_object_index, project_execution_context
from studio.child_drawing_memory import drawing_memory_context
from studio.child_execution import execution_context
from studio.drawing_dsl import DrawingAssignment
from studio.eraser import element_geometry
from studio.runtime import DrawingRuntime
from studio.settings import ModelConfig, ModelSettings
from studio.store import StudioStore
from studio.vision import scene_digest


def test_current_native_geometry_and_styles_survive_compaction_exactly(tmp_path, native_drawing_child):
    async def check():
        store = StudioStore(tmp_path / 'native-index.sqlite3')
        runtime = DrawingRuntime(store, ModelSettings(store))
        session = store.create_session()
        task = store.create_task(session['id'])
        child = native_drawing_child(store, runtime, session, task)
        try:
            made = child.call('draw_paths', {'paths': [
                {'name': f'轮廓 {index}', 'smooth': False,
                 'points': [[20 + index * 20, 40], [35 + index * 20, 60], [50 + index * 20, 40]]}
                for index in range(10)]}, 'actual-strokes')
            assert made['ok']
            assert child.call('move_elements', {'ids': made['changed_ids'][:2], 'dx': 5.125, 'dy': 17.5}, 'move')['ok']
            assert child.call('update_elements', {'ids': [made['changed_ids'][0]],
                'patch': {'angle': 0.31, 'strokeColor': '#784c30', 'opacity': 65}}, 'actual-style')['ok']
            index = json.loads(drawing_memory_context(runtime.memory, child.run, [])['content'])[
                'drawing_execution_memory']['current_object_index']
            original = copy.deepcopy(index)
            compact = compact_object_index(index)
            assert index == original
            assert len(json.dumps(compact)) < len(json.dumps(index))
            assert compact['object_defaults'] and compact['polyline_defaults']
            native = {item['id']: item for item in child.run['draft']}
            restored = []
            for item in compact['objects']:
                current = {**compact['object_defaults'], **item}
                current['current_polyline'] = {**compact['polyline_defaults'], **current['current_polyline']}
                restored.append(current)
                path = current['current_polyline']
                assert LineString(path['points']).hausdorff_distance(element_geometry(native[current['id']])) <= 1
            assert restored == index['objects']
            assert compact_object_index(compact) == compact
            assert store.get_document(session['document']['id'])['elements'] == []
        finally:
            await runtime.close()
            store.close()
    asyncio.run(check())


def test_original_art_direction_and_all_criteria_remain_in_the_assignment():
    recorded = json.loads((Path(__file__).parent / 'fixtures/recorded_report_failures.json').read_text(encoding='utf-8'))
    assignment = DrawingAssignment.model_validate_json(recorded['assignment'])
    spec = assignment.drawspec.model_dump(mode='json')
    messages = [{'role': 'system', 'content': 'Drawing child'},
        {'role': 'user', 'content': json.dumps({'drawspec': spec})}]
    original = copy.deepcopy(messages)
    projected = execution_context({'draft': recorded['native_elements']}, messages)
    prompt = json.loads(projected[1]['content'])
    assert messages == original and prompt['drawspec'] == spec
    refs = prompt['art_execution']['original_fields']
    assert 'direction' in refs and 'stage_criteria' in refs
    expected = {'direction': spec['composition']['art_direction'],
        'stage_criteria': spec['composition']['stages'][0]['criteria'],
        'success_criteria': spec['success_criteria'], 'value_design': spec['composition']['value_groups']}
    for key, path in refs.items():
        value = prompt
        for part in path:
            value = value[part]
        assert value == expected[key]
    for key in ('visual_focus', 'expression', 'unity', 'selective_detail', 'artistic_language'):
        assert prompt['art_execution']['decisions'][key]
    assert prompt['art_execution']['painting_sequence']
    assert not prompt['current_visual_evidence']['full_current_image_attached']


def test_only_attached_current_private_images_count_and_raw_errors_are_preserved(tmp_path, native_drawing_child):
    async def check():
        store = StudioStore(tmp_path / 'visual-receipts.sqlite3')
        runtime = DrawingRuntime(store, ModelSettings(store))
        session = store.create_session()
        task = store.create_task(session['id'])
        child = native_drawing_child(store, runtime, session, task)
        try:
            assert child.call('create_element', {'type': 'ellipse', 'x': 30, 'y': 50,
                'width': 100, 'height': 80}, 'original')['ok']
            stale, _ = await runtime.vision.context(task['id'], child.run['agent_id'], child.run['id'], child.document)
            assert child.call('move_elements', {'ids': [child.run['draft'][0]['id']], 'dx': 12, 'dy': 15}, 'move')['ok']
            current, ids = await runtime.vision.context(task['id'], child.run['agent_id'], child.run['id'], child.document)
            failure = child.call('move_elements', {'ids': ['missing-native-id'], 'dx': 1, 'dy': 1}, 'actual-error')
            assert not failure['ok']
            error = {'role': 'tool', 'tool_call_id': 'actual-error', 'content': json.dumps(failure)}
            models = DrawingAgents(store, ModelConfig(provider='ollama', model='llama3.1', api_base='http://localhost:11434'))
            messages = models.child_messages(child.run, store.agents.get_agent(session['id'], child.agent['agent_id']))
            messages.extend([*stale, *current, error])
            messages.append(drawing_memory_context(runtime.memory, child.run, messages))
            before = copy.deepcopy(messages)
            projected = execution_context(child.run, messages)
            assert messages == before
            assert projected[2:-1] == messages[2:-1]
            prompt = json.loads(projected[1]['content'])
            evidence = json.loads(projected[-1]['content'])['drawing_execution_memory']['current_visual_evidence']
            assert evidence['full_current_image_attached']
            assert evidence['scene_hash'] == scene_digest(child.document)
            assert [item['observation_id'] for item in evidence['attached_current_images']] == ids
            assert error in projected
            assert project_execution_context(child.run, prompt, projected) == projected
            declared = DrawingAssignment.model_validate_json(json.loads((Path(__file__).parent /
                'fixtures/recorded_report_failures.json').read_text(encoding='utf-8'))['assignment'])
            child.run['message'] = declared.model_dump_json()
            with_report = models.child_messages(child.run, store.agents.get_agent(session['id'], child.agent['agent_id']))
            original_report = json.loads(with_report[1]['content'])['drawing_report']
            with_report.extend([*current, error, messages[-1]])
            ordered = execution_context(child.run, with_report)
            ordered_prompt = json.loads(ordered[1]['content'])
            last = json.loads(ordered[-1]['content'])['drawing_execution_memory']
            assert 'drawing_report' not in ordered_prompt
            assert last['drawing_report']['format'] == original_report['format']
            assert last['drawing_report']['required_form_ids'] == original_report['required_form_ids']
            assert last['drawing_report']['allowed_constraint_ids'] == original_report['allowed_constraint_ids']
            assert last['drawing_report']['current_stage_id'] == declared.drawspec.stage_id
            stages = declared.drawspec.composition.stages
            position = next(index for index, stage in enumerate(stages) if stage.id == declared.drawspec.stage_id)
            assert last['drawing_report']['later_stage_ids'] == [stage.id for stage in stages[position + 1:]]
            assert error in ordered and ordered[2:-1] == with_report[2:-1]
        finally:
            await runtime.close()
            store.close()
    asyncio.run(check())
