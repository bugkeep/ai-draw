import asyncio
import copy
import json
import time
from pathlib import Path

from shapely import LineString

from studio.agents import DrawingAgents, scene_context
from studio.actions import element_fingerprint
from studio.child_art_context import artistic_brief, indexed_summary, perspective_references
from studio.child_drawing_memory import drawing_memory_context
from studio.child_execution import continue_repair, execution_context, whole_canvas_verification
from studio.drawing_dsl import DrawingAssignment
from studio.eraser import element_geometry
from studio.runtime import DrawingRuntime
from studio.settings import ModelConfig, ModelSettings
from studio.store import StudioStore
from studio.vision import scene_digest


def recorded_assignment():
    recorded = json.loads((Path(__file__).parent / 'fixtures/recorded_report_failures.json').read_text(encoding='utf-8'))
    return DrawingAssignment.model_validate_json(recorded['assignment'])


def test_art_brief_preserves_actual_direction_scope_and_later_work():
    request = recorded_assignment()
    spec = request.drawspec.model_dump()
    original = copy.deepcopy(spec)
    brief = artistic_brief({'drawspec': spec})
    assert spec == original
    assert brief['direction'] == spec['composition']['art_direction']
    assert brief['value_design'] == spec['composition']['value_groups']
    assert brief['phase'] == 'block_in'
    assert brief['stage_criteria'] == spec['composition']['stages'][0]['criteria']
    assert brief['success_criteria'] == spec['success_criteria']
    assert [item['id'] for item in brief['later_planned_stages']] == [
        item['id'] for item in spec['composition']['stages'][1:]]
    for intent in ('edit', 'research'):
        assert artistic_brief({'drawspec': {**spec, 'intent': intent}}) is None
    diagram = copy.deepcopy(spec)
    for subject in diagram['composition']['subjects']:
        subject['category'] = 'diagram'
    assert artistic_brief({'drawspec': diagram}) is None


def test_native_styles_roundtrip_and_only_redundant_prefix_metadata_is_removed(tmp_path, native_drawing_child):
    async def check():
        store = StudioStore(tmp_path / 'appearance.sqlite3')
        runtime = DrawingRuntime(store, ModelSettings(store))
        session = store.create_session()
        task = store.create_task(session['id'])
        child = native_drawing_child(store, runtime, session, task)
        try:
            made = child.call('create_elements', {'items': [
                {'type': 'ellipse', 'name': '前景形体', 'x': 20, 'y': 45, 'width': 110, 'height': 30,
                 'strokeColor': '#2b2420', 'backgroundColor': '#faf5e9', 'strokeWidth': 2, 'opacity': 90},
                {'type': 'text', 'name': '店招', 'text': 'CAFE', 'fontSize': 23},
                {'type': 'ellipse', 'name': '重复样式', 'x': 200, 'y': 45, 'width': 110, 'height': 30,
                 'strokeColor': '#2b2420', 'backgroundColor': '#faf5e9', 'strokeWidth': 2, 'opacity': 90}]}, 'native-styles')
            assert made['ok']
            child.run['relevant_ids'] = made['changed_ids']
            models = DrawingAgents(store, ModelConfig(provider='ollama', model='llama3.1', api_base='http://localhost:11434'))
            messages = models.child_messages(child.run, store.agents.get_agent(session['id'], child.agent['agent_id']))
            original = copy.deepcopy(messages)
            journal = drawing_memory_context(runtime.memory, child.run, messages)
            index = json.loads(journal['content'])['drawing_execution_memory']['current_object_index']
            styles = {item['id']: item for item in index['styles']}
            restored = [indexed_summary(item, styles) for item in index['objects']]
            assert restored == scene_context(child.document, made['changed_ids'])['elements']
            assert len(styles) == 2
            failure = child.call('move_elements', {'ids': ['missing-native-id'], 'dx': 1, 'dy': 0}, 'native-error')
            assert failure['ok'] is False
            recorded = json.loads((Path(__file__).parent / 'fixtures/recorded_ineffective_draft.json').read_text(encoding='utf-8'))
            completion = {'completion': {'status': 'blocked', 'review_status': recorded['review']['status']}}
            assert continue_repair(completion, {'unresolved': [], 'checks': []})
            assert not continue_repair(completion, {'unresolved': [failure['error']], 'checks': []})
            assert not continue_repair({'completion': {**completion['completion'],
                'reason': 'ineffective_repair'}}, {'unresolved': [], 'checks': []})
            assert completion['completion']['status'] == 'blocked'
            error = {'role': 'user', 'content': json.dumps(failure, ensure_ascii=False)}
            pictures, _ = await runtime.vision.context(task['id'], child.run['agent_id'], child.run['id'], child.document)
            pixels = pictures[0]
            result = execution_context(child.run, [*messages, error, pixels, journal])
            assert messages == original
            prompt = json.loads(result[1]['content'])
            assert prompt['scene']['elements'] == [] and not prompt['scene']['has_more']
            assert prompt['scene']['object_index_refs'] == [item['id'] for item in json.loads(original[1]['content'])['scene']['elements']]
            assert prompt['relevant_object_index_refs'] == made['changed_ids']
            assert result[-3:] == [error, pixels, journal]
            assert execution_context(child.run, [*messages, error])[-1] == error
            exact = copy.deepcopy(messages)
            prefix = json.loads(exact[1]['content'])
            prefix['relevant_elements'] = scene_context(child.document, made['changed_ids'], include_geometry=True)['elements']
            prefix['relevant_elements'][0]['extra_native_fact'] = 'retain'
            exact[1]['content'] = json.dumps(prefix)
            kept = json.loads(execution_context(child.run, [*exact, journal])[1]['content'])
            assert kept['relevant_elements'][0] == prefix['relevant_elements'][0]
            assert child.call('update_elements', {'ids': [made['changed_ids'][0]],
                'patch': {'strokeColor': '#6a3a20', 'strokeWidth': 0.7}}, 'refine-style')['ok']
            assert child.call('delete_elements', {'ids': [made['changed_ids'][-1]]}, 'remove-duplicate-style')['ok']
            current = json.loads(drawing_memory_context(runtime.memory, child.run, [])['content'])['drawing_execution_memory']['current_object_index']
            current_styles = {item['id']: item for item in current['styles']}
            assert [indexed_summary(item, current_styles) for item in current['objects']] == scene_context(
                child.document, [made['changed_ids'][0], made['changed_ids'][1]])['elements']
            assert made['changed_ids'][-1] not in {item['id'] for item in current['objects']}
        finally:
            await runtime.close()
            store.close()
    asyncio.run(check())


def test_projection_references_use_declared_vanishing_points_without_drawing_actions():
    composition = {
        'subjects': [{'id': 'building', 'category': 'architecture',
                      'bounds': {'x': 240, 'y': 100, 'width': 1000, 'height': 680}}],
        'forms': [{'id': 'corner', 'subject_id': 'building'}],
        'landmarks': [{'id': 'top', 'form_id': 'corner', 'role': 'apex', 'x': 690, 'y': 130}],
        'perspective': {'vanishing_points': [[-420, 560], [1980, 560]]}}
    original = copy.deepcopy(composition)
    references = perspective_references(composition, ['corner'])
    assert composition == original
    assert references['coordinate_space'] == 'canvas'
    assert len(references['rays']) == 2
    for ray in references['rays']:
        (x, y), (ex, ey) = ray['canvas_ray']
        vx, vy = composition['perspective']['vanishing_points'][ray['vanishing_point_index']]
        assert abs((ey - y) * (vx - x) - (vy - y) * (ex - x)) < 0.1
    assert perspective_references(composition, ['another-form'])['rays'] == []
    composition['perspective']['vanishing_points'] = []
    assert perspective_references(composition, ['corner']) is None


def test_real_regional_observation_does_not_request_whole_pass_verification(tmp_path, native_drawing_child):
    async def check():
        store = StudioStore(tmp_path / 'observations.sqlite3')
        runtime = DrawingRuntime(store, ModelSettings(store))
        session = store.create_session()
        task = store.create_task(session['id'])
        child = native_drawing_child(store, runtime, session, task)
        try:
            assert child.call('create_element', {'type': 'ellipse', 'x': 30, 'y': 50, 'width': 100, 'height': 80}, 'form')['ok']
            for call_id, arguments in [('detail', {'region': {'x': 20, 'y': 40, 'width': 120, 'height': 100}}), ('whole', {})]:
                encoded = json.dumps(arguments)
                assert store.agents.begin_call(task['id'], child.run['id'], call_id, 'view_canvas', encoded) is None
                output = await runtime.vision.observe(task['id'], child.run['agent_id'], child.run['id'],
                    child.document, arguments.get('region'), remember_region=True)
                assert output['ok']
                store.agents.finish_call(child.run['id'], call_id, output, child.run)
                row = store.connection.execute('SELECT name,arguments,output FROM agent_tool_calls WHERE caller_id=? AND call_id=?',
                    (child.run['id'], call_id)).fetchone()
                assert whole_canvas_verification(row) == (call_id == 'whole')
            assert not whole_canvas_verification(None)
            assert len(child.run['prepared']) == 1
            assert store.get_document(session['document']['id'])['elements'] == []
        finally:
            await runtime.close()
            store.close()
    asyncio.run(check())


def test_current_polyline_matches_native_path_after_move_and_rotation(tmp_path, native_drawing_child):
    async def check():
        store = StudioStore(tmp_path / 'current-path.sqlite3')
        runtime = DrawingRuntime(store, ModelSettings(store))
        session = store.create_session()
        task = store.create_task(session['id'])
        child = native_drawing_child(store, runtime, session, task)
        try:
            made = child.call('draw_paths', {'paths': [{'name': '立面轮廓', 'smooth': False, 'closed': True,
                'points': [[240, 304], [690, 130], [690, 760], [240, 679]]}]}, 'actual-plane')
            assert made['ok']
            assert child.call('move_elements', {'ids': made['changed_ids'], 'dx': 12, 'dy': 82}, 'move-plane')['ok']
            assert child.call('update_elements', {'ids': made['changed_ids'], 'patch': {'angle': 0.7}}, 'rotate-plane')['ok']
            before = copy.deepcopy(child.run)
            changes = store.connection.total_changes
            journal = json.loads(drawing_memory_context(runtime.memory, child.run, [])['content'])['drawing_execution_memory']
            polyline = journal['current_object_index']['objects'][0]['current_polyline']
            native = next(item for item in child.run['draft'] if item['id'] == made['changed_ids'][0])
            assert polyline['coordinate_space'] == 'canvas' and polyline['max_centerline_error'] == 1
            assert LineString(polyline['points']).hausdorff_distance(element_geometry(native)) <= 1
            assert child.run == before and store.connection.total_changes == changes
        finally:
            await runtime.close()
            store.close()
    asyncio.run(check())


def test_compared_ineffective_repair_hands_back_actual_pending_draft_without_a_model_request(tmp_path, native_drawing_child):
    async def check():
        store = StudioStore(tmp_path / 'handoff.sqlite3')
        runtime = DrawingRuntime(store, ModelSettings(store))
        session = store.create_session()
        task = store.create_task(session['id'])
        child = native_drawing_child(store, runtime, session, task)
        try:
            assert child.call('create_element', {'type': 'ellipse', 'name': '未提交形体'}, 'actual-outline')['ok']
            before = copy.deepcopy(child.run)
            # 真实评审记录用于检查交接执行及持久状态，不评价此原生测试形体的画质。
            recorded = json.loads((Path(__file__).parent / 'fixtures/draft-review-replan-response.json').read_text(encoding='utf-8'))
            feedback = {**recorded['result'], 'scene_hash': scene_digest(child.document),
                        'selection': child.run['selection']}
            with store.lock, store.connection:
                store.connection.execute('INSERT INTO draft_reviews VALUES(?,?,?,?,?,?,NULL)', (
                    feedback['review_key'], task['id'], child.run['id'], feedback['scene_hash'],
                    json.dumps(feedback), time.time()))
            models = DrawingAgents(store, ModelConfig(provider='ollama', model='llama3.1', api_base='http://localhost:11434'))
            await runtime.children._execute_run(models, session['id'], child.run)
            saved = store.agents.get_run(session['id'], child.run['id'])
            assert saved['status'] == 'completed' and saved['prepared'] == before['prepared']
            assert saved['draft'] == before['draft']
            result = store.agents.get_agent(session['id'], child.agent['agent_id'])['latest_result']
            assert result['completion']['status'] == 'blocked'
            assert result['completion']['reason'] == 'ineffective_repair'
            assert result['completion']['source'] == 'independent_draft_comparison'
            assert not store.connection.execute("SELECT 1 FROM tool_logs WHERE kind='llm_response'").fetchall()
            assert store.get_document(session['document']['id'])['elements'] == []
            continued = native_drawing_child(store, runtime, session, task, child.agent)
            assert continued.run['document']['depends_on'] == [result['result_id']]
            assert [element_fingerprint(item) for item in continued.run['draft']] == [
                element_fingerprint(item) for item in before['draft']]
            assert continued.run['prepared'] == []
            assert store.connection.execute('SELECT count(*) FROM agent_tool_calls').fetchone()[0] == 1
        finally:
            await runtime.close()
            store.close()
    asyncio.run(check())
