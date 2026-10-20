import asyncio
import json

import pytest

from studio.agent_tools import MAIN_TOOL_NAMES, CHILD_TOOL_NAMES, validate_tool
from studio.agents import DrawingAgents, prepare_drawing_tool
from studio.drawing_dsl import DrawSpec, assignment, latest_spec, prepare_assignment, parse_report, result_evidence
from studio.child_harness import coordinate_context
from studio.runtime import DrawingRuntime
from studio.reference_work import ReferenceWork
from studio.settings import ModelConfig, ModelSettings
from studio.store import StudioStore


def brief(phase='structure', revision=1):
    return {'stage_id': 'block_in' if phase in {'structure', 'repair'} else 'contour',
        'intent': phase if phase in {'repair', 'edit'} else 'execute',
        'composition': {'revision': revision,
        'frame': {'x': 0, 'y': 0, 'width': 1000, 'height': 800},
        'art_direction': {'medium': 'ink', 'rendering': '黑白线稿', 'focal_intent': '窗台上蜷睡的猫',
            'line_quality': '轮廓连续清晰，内部线条区分前后关系', 'value_strategy': '白纸与黑线，前景遮挡背景',
            'finish_criteria': ['完整蜷睡姿态与接触关系', '连贯外轮廓，尾巴与躯干连接，背景不穿过主体']},
        'subjects': [
            {'id': 'cat', 'description': '蜷睡的猫', 'category': 'animal', 'role': 'focal',
                'bounds': {'x': 250, 'y': 300, 'width': 450, 'height': 300}},
            {'id': 'sill', 'description': '承托猫的窗台', 'category': 'architecture', 'role': 'supporting',
                'bounds': {'x': 100, 'y': 600, 'width': 800, 'height': 100}},
            {'id': 'branch', 'description': '窗外树枝', 'category': 'environment', 'role': 'setting',
                'bounds': {'x': 100, 'y': 100, 'width': 800, 'height': 500}}],
        'forms': [
            {'id': 'cat_head', 'subject_id': 'cat', 'form': 'ovoid', 'description': '低垂头部',
                'bounds': {'x': 250, 'y': 350, 'width': 130, 'height': 130}},
            {'id': 'cat_body', 'subject_id': 'cat', 'form': 'ovoid', 'description': '蜷曲躯干',
                'bounds': {'x': 360, 'y': 320, 'width': 340, 'height': 280}},
            {'id': 'cat_tail', 'subject_id': 'cat', 'form': 'ribbon', 'description': '从后躯围绕腹部的尾巴',
                'bounds': {'x': 300, 'y': 500, 'width': 400, 'height': 100}},
            {'id': 'sill_plane', 'subject_id': 'sill', 'form': 'plane', 'description': '水平承托面',
                'bounds': {'x': 100, 'y': 600, 'width': 800, 'height': 100}},
            {'id': 'branch_mass', 'subject_id': 'branch', 'form': 'mass', 'description': '窗外分叉树枝',
                'bounds': {'x': 100, 'y': 100, 'width': 800, 'height': 500}}],
        'gestures': [{'subject_id': 'cat', 'line_of_action': [[320, 400], [520, 350], [650, 500], [450, 550]],
            'description': '背部弧线围绕低垂的头部'}],
        'landmarks': [
            {'id': 'belly_contact', 'form_id': 'cat_body', 'role': 'contact', 'x': 500, 'y': 600, 'description': '腹部接触面'},
            {'id': 'sill_contact', 'form_id': 'sill_plane', 'role': 'contact', 'x': 500, 'y': 600, 'description': '窗台接触点'},
            {'id': 'head_neck', 'form_id': 'cat_head', 'role': 'joint', 'x': 370, 'y': 400, 'description': '头颈连接'},
            {'id': 'body_neck', 'form_id': 'cat_body', 'role': 'joint', 'x': 370, 'y': 400, 'description': '躯干颈部连接'}],
        'proportions': [{'id': 'head_body_ratio', 'source': 'cat_head', 'target': 'cat_body', 'axis': 'width',
            'ratio': 130 / 340, 'tolerance': 0.05, 'description': '头部宽度服从蜷睡形体'}],
        'relations': [
            {'id': 'belly_support', 'kind': 'supported_by', 'source': 'belly_contact', 'target': 'sill_contact', 'description': '腹部接触台面'},
            {'id': 'neck_join', 'kind': 'attached_to', 'source': 'head_neck', 'target': 'body_neck', 'description': '头颈自然连接'},
            {'id': 'branch_depth', 'kind': 'behind', 'source': 'branch', 'target': 'cat', 'description': '树枝不穿过猫身'}],
        'value_groups': [{'id': 'cat_white', 'member_ids': ['cat_head', 'cat_body'], 'value': 1, 'description': '猫的白色形体遮挡背景'}],
        'stages': [
            {'id': 'block_in', 'phase': 'block_in', 'criteria': ['完整动态与主要形体，连接和支撑准确']},
            {'id': 'contour', 'phase': 'contour', 'criteria': ['主体外轮廓连续，前后遮挡清晰']},
            {'id': 'finish', 'phase': 'detail', 'criteria': ['完成闭眼、爪部与树枝的必要特征']}],
        'preserve': ['保留窗台和窗外树枝']},
        'form_ids': [] if phase == 'structure' else ['cat_head', 'cat_body', 'cat_tail'],
        'success_criteria': ['本阶段建立完整蜷睡姿态、支撑与前后遮挡；细节由后续输入完成']}


def test_existing_tools_carry_validated_common_structure_without_child_messaging():
    for name, extra in [('spawn_agent', {}), ('send_input', {'agent_id': 'existing-agent'})]:
        value = validate_tool(name, {'message': '建立整体结构', 'drawspec': brief(), **extra})
        assert DrawSpec.model_validate(value['drawspec']).composition.revision == 1
    assert 'send_input' in MAIN_TOOL_NAMES and 'send_input' not in CHILD_TOOL_NAMES
    assert 'spawn_agent' not in CHILD_TOOL_NAMES
    for invalid in ['unknown_subject', 'duplicate_id', 'cycle', 'nested_cycle', 'outside_frame', 'bad_proportion',
            'detached_contact', 'missing_gesture', 'hierarchy_cycle', 'invalid_stage', 'unknown_protocol']:
        value = brief()
        if invalid == 'unknown_subject':
            value['composition']['relations'][0]['target'] = 'missing'
        elif invalid == 'duplicate_id':
            value['composition']['landmarks'][0]['id'] = 'cat'
        elif invalid == 'cycle':
            value['composition']['relations'].append({'id': 'cycle', 'kind': 'behind', 'source': 'cat', 'target': 'branch', 'description': '冲突'})
        elif invalid == 'nested_cycle':
            value['composition']['relations'].append({'id': 'cycle', 'kind': 'behind', 'source': 'cat_body', 'target': 'branch_mass', 'description': '主体与子形体之间的遮挡循环'})
        elif invalid == 'outside_frame':
            value['composition']['subjects'][0]['bounds']['x'] = 900
        elif invalid == 'bad_proportion':
            value['composition']['proportions'][0]['ratio'] = 2
        elif invalid == 'detached_contact':
            value['composition']['landmarks'][1]['y'] = 620
        elif invalid == 'missing_gesture':
            value['composition']['gestures'] = []
        elif invalid == 'hierarchy_cycle':
            value['composition']['forms'][0]['parent_id'] = 'cat_body'
            value['composition']['forms'][1]['parent_id'] = 'cat_head'
        elif invalid == 'invalid_stage':
            value['stage_id'] = 'missing'
        else:
            value['protocol'] = 'drawspec/2'
        with pytest.raises(ValueError):
            validate_tool('spawn_agent', {'message': '建立结构', 'drawspec': value})


def test_composition_reports_all_numeric_conflicts_before_dispatch():
    value = brief()
    value['composition']['landmarks'][1]['y'] = 620
    value['composition']['proportions'][0]['ratio'] = 2
    with pytest.raises(ValueError) as failed:
        DrawSpec.model_validate(value)
    assert 'belly_support' in str(failed.value) and 'head_body_ratio' in str(failed.value)
    assert 'distance=20' in str(failed.value)
    subject_ratio = brief()
    subject_ratio['composition']['proportions'][0].update(target='cat', ratio=130 / 450)
    assert DrawSpec.model_validate(subject_ratio).composition.proportions[0].target == 'cat'


def test_failed_assignment_accepts_scoped_record_amendments_without_resending_plan(tmp_path):
    async def check():
        store = StudioStore(tmp_path / 'amendment.sqlite3')
        runtime = DrawingRuntime(store, ModelSettings(store))
        session = store.create_session()
        task = store.create_task(session['id'])
        models = DrawingAgents(store, ModelConfig(provider='ollama', model='unused'))
        try:
            invalid = brief()
            invalid['composition']['landmarks'][1]['y'] = 620
            invalid['composition']['proportions'][0]['ratio'] = 2
            original = {'message': '建立整体结构', 'drawspec': invalid}
            call = {'id': 'invalid-plan', 'function': {'name': 'spawn_agent', 'arguments': json.dumps(original)}}
            output = await runtime.call_main_tool(models, task['id'], session['id'], session['document']['id'], call)
            assert output['ok'] is False and output['drawspec_draft_call_id'] == call['id']
            assert 'belly_support' in output['error'] and 'head_body_ratio' in output['error']
            assert store.agents.list_agents(session['id']) == []
            correction = {'stage_id': 'block_in', 'success_criteria': brief()['success_criteria'], 'amend': {
                'source_call_id': call['id'], 'landmarks': [brief()['composition']['landmarks'][1]],
                'proportions': brief()['composition']['proportions']}}
            validated = validate_tool('spawn_agent', {'message': original['message'], 'drawspec': correction})
            packed = prepare_assignment(store, session['id'], task['id'], validated['message'], validated['drawspec'])
            assert assignment(packed).drawspec.composition == DrawSpec.model_validate(brief()).composition
            assert len(json.dumps(correction)) < len(json.dumps(invalid)) / 3
            recorded = store.connection.execute('SELECT arguments FROM agent_tool_calls WHERE call_id=?', (call['id'],)).fetchone()
            assert json.loads(recorded['arguments']) == original
            other_task = store.create_task(session['id'])
            with pytest.raises(ValueError, match='failed main assignment in this task'):
                prepare_assignment(store, session['id'], other_task['id'], '另一任务不可读取草案', correction)
            correction['amend']['remove_ids'] = ['undeclared']
            with pytest.raises(ValueError, match='undeclared records'):
                prepare_assignment(store, session['id'], task['id'], '不能删除未知记录', correction)
            assert store.get_document(session['document']['id'])['elements'] == []
        finally:
            await runtime.close()
            store.close()
    asyncio.run(check())


def test_queued_sync_keeps_current_input_private_and_requires_submitted_structure(tmp_path):
    async def check():
        path = tmp_path / 'composition.sqlite3'
        store = StudioStore(path)
        runtime = DrawingRuntime(store, ModelSettings(store))
        session = store.create_session()
        task = store.create_task(session['id'])
        models = DrawingAgents(store, ModelConfig(provider='ollama', model='unused'))
        try:
            packed = prepare_assignment(store, session['id'], task['id'], '建立整幅结构', brief())
            actor = store.agents.create_agent(session['id'], task['id'], 'initial', packed, 'Structure', [])
            run = runtime.children._initialize(session['id'], store.agents.next_run(session['id'], actor['agent_id'], task['id']))
            prompt = json.loads(models.child_messages(run, store.agents.get_agent(session['id'], actor['agent_id']))[1]['content'])
            assert prompt['goal'] == '建立整幅结构'
            assert prompt['drawspec'] == DrawSpec.model_validate(brief()).model_dump(mode='json')
            assert prompt['drawing_report']['required_form_ids'] == [form['id'] for form in brief()['composition']['forms']]
            reference = {'stage_id': 'block_in', 'composition_ref': prompt['composition_id'],
                'success_criteria': ['继续检查同一整体结构']}
            short = validate_tool('send_input', {'agent_id': actor['agent_id'], 'message': '下一次结构检查',
                'drawspec': reference})
            queued_message = prepare_assignment(store, session['id'], task['id'], short['message'], short['drawspec'])
            assert assignment(queued_message).drawspec.composition_id == prompt['composition_id']
            assert assignment(queued_message).drawspec.composition == assignment(packed).drawspec.composition
            assert len(json.dumps(short)) < len(packed) / 2
            with pytest.raises(ValueError, match='communicates through DrawSpec'):
                prepare_assignment(store, session['id'], task['id'], '不能丢失当前构图和阶段', None)
            store.agents.enqueue_input(session['id'], task['id'], actor['agent_id'], 'queued', queued_message, [])
            assert store.agents.get_run(session['id'], run['id'])['message'] == packed
            assert store.agents.next_run(session['id'], actor['agent_id'], task['id'])['id'] == run['id']
            assert len(store.agents.get_agent(session['id'], actor['agent_id'])['inputs']) == 2
            changed = brief()
            changed['composition']['art_direction']['line_quality'] = '不同线条要求'
            for value in [brief(revision=2), changed]:
                with pytest.raises(ValueError):
                    prepare_assignment(store, session['id'], task['id'], '改变整体构图', value)
            with pytest.raises(ValueError, match='structure result'):
                prepare_assignment(store, session['id'], task['id'], '局部完善', brief('refinement'))
            # 使用原生执行器和真实存储验证阶段条件，不生成模型回复或质量评分。
            args = {'type': 'ellipse', 'name': 'cat body structure', 'x': 250, 'y': 300, 'width': 450, 'height': 300}
            assert store.agents.begin_call(task['id'], run['id'], 'body', 'create_element', json.dumps(args)) is None
            draft, selected, actions, outcome = prepare_drawing_tool('create_element', args, run['document'], run['draft'], run['selection'])
            run.update(draft=draft, selection=selected, prepared=actions)
            store.agents.finish_call(run['id'], 'body', outcome, run)
            result = store.agents.complete_run(session['id'], run['id'], '结构原生动作准备完成')
            assert store.get_document(session['document']['id'])['elements'] == []
            repair = prepare_assignment(store, session['id'], task['id'], '修复私有结构再预览', brief('repair'),
                agent_id=actor['agent_id'])
            assert assignment(repair).drawspec.intent == 'repair'
            with pytest.raises(ValueError, match='pending private candidate'):
                prepare_assignment(store, session['id'], task['id'], '另一子 Agent 的私有结构不可继承', brief('repair'))
            with pytest.raises(ValueError, match='structure result'):
                prepare_assignment(store, session['id'], task['id'], '局部完善', brief('refinement'))
            await runtime.apply_result(task['id'], session['id'], session['document']['id'], result['result_id'])
            assert len(store.get_document(session['document']['id'])['elements']) == 1
            queued = runtime.children._initialize(session['id'], store.agents.next_run(session['id'], actor['agent_id'], task['id']))
            assert assignment(queued['message']).goal == '下一次结构检查'
            store.agents.complete_run(session['id'], queued['id'], '结构消息已处理')
            follow_up = prepare_assignment(store, session['id'], task['id'], '完善当前猫的轮廓', brief('refinement'))
            store.agents.enqueue_input(session['id'], task['id'], actor['agent_id'], 'refine', follow_up, outcome['changed_ids'])
            assert latest_spec(store, task['id'])['drawspec']['stage_id'] == 'contour'
            parent = json.loads(models.main_messages(task['id'], '画猫', store.get_document(session['document']['id']), [], [])[1]['content'])
            assert parent['shared_drawspec']['composition_id'] == prompt['composition_id']
            other = store.create_session()
            other_task = store.create_task(other['id'])
            assert latest_spec(store, other_task['id'], include_previous=True) is None
            with pytest.raises(ValueError, match='current saved composition'):
                prepare_assignment(store, other['id'], other_task['id'], '无权读取另一画布构图', reference)
            with pytest.raises(KeyError):
                prepare_assignment(store, other['id'], task['id'], '跨画布消息', brief())
            next_task = store.create_task(session['id'])
            assert latest_spec(store, next_task['id'], include_previous=True)['from_previous_task'] is True
            continuation = prepare_assignment(store, session['id'], next_task['id'], '继续完善上一轮的轮廓', {
                'stage_id': 'contour', 'composition_ref': prompt['composition_id'], 'form_ids': ['cat_body'],
                'success_criteria': ['沿用已经提交的起形继续深化，不重复建立整幅构图']})
            assert assignment(continuation).drawspec.composition_id == prompt['composition_id']
        finally:
            await runtime.close()
            store.close()
        reopened = StudioStore(path)
        try:
            assert latest_spec(reopened, task['id'])['drawspec']['stage_id'] == 'contour'
            assert reopened.agents.get_run(session['id'], run['id'])['message'] == packed
        finally:
            reopened.close()
    asyncio.run(check())


def test_reference_work_keeps_own_source_views_through_normal_assignment_message(tmp_path):
    from test_reference_work import workspace

    async def check():
        store, runtime, session, task, layout, original = workspace(tmp_path)
        try:
            packed = prepare_assignment(store, session['id'], task['id'], original, brief())
            work = ReferenceWork.load(runtime.references, task['id'], packed)
            assert work.assignment.reference_layout_id == layout['reference_layout_id']
            assert work.assignment.goal == json.loads(original)['goal']
            work.prepare_source('current-input')
            pictures, ids = await runtime.references.prepare_context(task['id'], 'current-input')
            assert len(ids) == 2 and pictures
            assert all(row['delivered_at'] is None for row in store.connection.execute(
                'SELECT delivered_at FROM reference_image_views WHERE caller_id=?', ('current-input',)))
            assert runtime.references.context(task['id'], 'other-input')[1] == []
        finally:
            await runtime.close()
            store.close()
    asyncio.run(check())


def test_resumed_old_composition_cannot_overwrite_new_structure(tmp_path):
    async def check():
        store = StudioStore(tmp_path / 'stale-composition.sqlite3')
        runtime = DrawingRuntime(store, ModelSettings(store))
        session = store.create_session()
        task = store.create_task(session['id'])
        try:
            packed = prepare_assignment(store, session['id'], task['id'], '旧结构', brief())
            actor = store.agents.create_agent(session['id'], task['id'], 'old', packed, 'Old', [])
            run = runtime.children._initialize(session['id'], store.agents.next_run(session['id'], actor['agent_id'], task['id']))
            args = {'type': 'ellipse', 'name': 'old structure', 'x': 250, 'y': 300}
            store.agents.begin_call(task['id'], run['id'], 'old-body', 'create_element', json.dumps(args))
            draft, selected, actions, output = prepare_drawing_tool('create_element', args, run['document'], run['draft'], run['selection'])
            run.update(draft=draft, selection=selected, prepared=actions)
            store.agents.finish_call(run['id'], 'old-body', output, run)
            store.agents.pause_agent(session['id'], actor['agent_id'])
            new_message = prepare_assignment(store, session['id'], task['id'], '新结构', brief(revision=2))
            store.agents.create_agent(session['id'], task['id'], 'new', new_message, 'New', [])
            store.agents.resume_agent(session['id'], task['id'], actor['agent_id'])
            resumed = store.agents.next_run(session['id'], actor['agent_id'], task['id'])
            assert resumed['id'] == run['id'] and resumed['message'] == packed
            result = store.agents.complete_run(session['id'], resumed['id'], '旧结构准备完成')
            before = store.get_document(session['document']['id'])
            with pytest.raises(ValueError, match='different composition revision'):
                await runtime.apply_result(task['id'], session['id'], session['document']['id'], result['result_id'])
            assert store.get_document(session['document']['id']) == before
            assert store.get_history(session['document']['id'], 'undo') == []
        finally:
            await runtime.close()
            store.close()
    asyncio.run(check())


def test_report_binds_current_objects_and_keeps_unfinished_structure_explicit(tmp_path):
    async def check():
        store = StudioStore(tmp_path / 'report.sqlite3')
        runtime = DrawingRuntime(store, ModelSettings(store))
        session = store.create_session()
        task = store.create_task(session['id'])
        try:
            packed = prepare_assignment(store, session['id'], task['id'], '建立结构', brief())
            actor = store.agents.create_agent(session['id'], task['id'], 'structure', packed, 'Structure', [])
            run = runtime.children._initialize(session['id'], store.agents.next_run(session['id'], actor['agent_id'], task['id']))
            args = {'type': 'ellipse', 'name': '躯干主要形体', 'x': 360, 'y': 320, 'width': 340, 'height': 280}
            draft, selected, actions, output = prepare_drawing_tool('create_element', args, run['document'], run['draft'], run['selection'])
            run.update(draft=draft, selection=selected, prepared=actions)
            object_id = output['changed_ids'][0]
            report = {'protocol': 'drawspec/1', 'kind': 'report', 'summary': '躯干形体已经建立，整体结构仍需完成',
                'bindings': [{'form_id': 'cat_body', 'object_ids': [object_id]}],
                'checks': [{'constraint_id': 'block_in', 'status': 'unresolved', 'evidence': '其他主要形体仍需建立'}],
                'unresolved': ['头部、尾巴与支撑关系仍未完成']}
            summary, parsed = parse_report(json.dumps(report), run)
            assert summary == report['summary'] and parsed['checks_are_artist_claims'] is True
            assert 'cat_head' in parsed['unbound_form_ids']
            assert parsed['composition_id'] == assignment(packed).drawspec.composition_id
            _, observation_ids = await runtime.vision.context(task['id'], actor['agent_id'], run['id'], {
                **run['document'], 'elements': draft, 'selection': selected})
            guide = json.loads(coordinate_context(store, run, observation_ids)['content'])['drawspec_coordinate_guide']
            assert guide['images'][0]['observation_id'] == observation_ids[0]
            observed = store.connection.execute('SELECT metadata FROM canvas_observations WHERE id=?', (observation_ids[0],)).fetchone()
            area = json.loads(observed['metadata'])['scene_bounds']
            contact = next(item for item in guide['images'][0]['landmarks'] if item['id'] == 'belly_contact')
            nx, ny = contact['image_normalized']
            assert [area['x'] + nx / 1000 * area['width'], area['y'] + ny / 1000 * area['height']] == pytest.approx([500, 600], abs=0.0001)
            assert 'branch_mass' in guide['images'][0]['outside_image_use_canvas']
            with pytest.raises(ValueError, match='exact current private-draft'):
                coordinate_context(store, {**run, 'id': 'another-input'}, observation_ids)
            with pytest.raises(ValueError, match='exact current private-draft'):
                coordinate_context(store, {**run, 'draft': []}, observation_ids)
            evidence = result_evidence(store, run, parsed, None)
            assert evidence['changed_ids'] == [object_id]
            assert evidence['image_evidence'] and all(not item['delivered_to_artist'] for item in evidence['image_evidence'])
            assert evidence['task_id'] == task['id'] and evidence['native_result_id'].startswith('result_')
            for invalid in ['foreign_id', 'unknown_form', 'unknown_constraint', 'missing_forms', 'duplicate_binding', 'version']:
                value = json.loads(json.dumps(report))
                if invalid == 'foreign_id':
                    value['bindings'][0]['object_ids'] = ['object-from-another-draft']
                elif invalid == 'unknown_form':
                    value['bindings'][0]['form_id'] = 'other_subject'
                elif invalid == 'unknown_constraint':
                    value['checks'][0]['constraint_id'] = 'invented'
                elif invalid == 'missing_forms':
                    value['unresolved'] = []
                elif invalid == 'duplicate_binding':
                    value['bindings'].append(value['bindings'][0])
                else:
                    value['protocol'] = 'drawspec/2'
                with pytest.raises(ValueError):
                    parse_report(json.dumps(value), run)
            assert parse_report('普通会话结果', {**run, 'message': '保持现有会话输入'}) == ('普通会话结果', None)
        finally:
            await runtime.close()
            store.close()
    asyncio.run(check())


@pytest.mark.parametrize('category', ['figure', 'landscape', 'diagram'])
def test_construction_vocabulary_is_not_specific_to_animal_artwork(category):
    value = brief()
    composition = value['composition']
    composition['subjects'][0]['category'] = category
    if category == 'figure':
        composition['subjects'][0]['description'] = '人物上半身构图'
        composition['forms'][0]['description'] = '头部卵形体'
        composition['forms'][1]['description'] = '胸廓形体'
    elif category == 'landscape':
        composition['gestures'] = []
        composition['perspective'] = {'projection': 'atmospheric', 'horizon_y': 350}
        composition['forms'][0]['description'] = '中景山体'
        composition['forms'][1]['description'] = '近景山体'
    else:
        composition['gestures'] = []
        composition['forms'][0]['form'] = 'box'
        composition['forms'][1]['form'] = 'box'
        composition['forms'][0]['description'] = '输入节点'
        composition['forms'][1]['description'] = '处理节点'
    parsed = DrawSpec.model_validate(value)
    assert parsed.composition.subjects[0].category == category
    assert parsed.composition.art_direction.finish_criteria
