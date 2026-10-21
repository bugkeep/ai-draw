import asyncio
import copy
import json
import math
from pathlib import Path

from shapely import LineString, Point, Polygon, box

from studio.child_art_context import artistic_brief
from studio.child_construction_context import construction_references
from studio.runtime import DrawingRuntime
from studio.settings import ModelSettings
from studio.store import StudioStore


def recorded_cafe():
    recorded = json.loads((Path(__file__).parent / 'fixtures/drawing-progress-recorded-cafe.json').read_text(encoding='utf-8'))
    return recorded['samples']['unfinished']['current']['drawspec']


def test_recorded_openings_fit_their_facade_and_converge_to_the_original_vanishing_points():
    spec = recorded_cafe()
    original = copy.deepcopy(spec)
    forms = {item['id']: item for item in spec['composition']['forms']}
    references = construction_references(spec, list(forms))
    assert spec == original
    planes = {item['form_id']: item for item in references['coplanar_forms']}
    assert set(planes) == {'left_window', 'right_window'}
    for key, reference in planes.items():
        assert reference['status'] == 'coplanar_reference'
        quad = reference['path']['points']
        assert reference['path']['closed'] and not reference['path']['smooth']
        bounds = forms[key]['bounds']
        assert box(bounds['x'], bounds['y'], bounds['x'] + bounds['width'], bounds['y'] + bounds['height']).covers(Polygon(quad))
        vp = spec['composition']['perspective']['vanishing_points'][reference['vanishing_point_index']]
        for a, b in ((quad[0], quad[1]), (quad[3], quad[2])):
            assert math.isclose((a[0] - vp[0]) * (b[1] - vp[1]),
                                (b[0] - vp[0]) * (a[1] - vp[1]), abs_tol=1e-7)
        corner_top = next(item for item in spec['composition']['landmarks'] if item['id'] == 'corner_top')
        corner_base = next(item for item in spec['composition']['landmarks'] if item['id'] == 'corner_base')
        vx, vy = vp
        for x, y in quad:
            scale = (x - vx) / (corner_top['x'] - vx)
            assert vy + (corner_top['y'] - vy) * scale <= y + 1e-9
            assert y <= vy + (corner_base['y'] - vy) * scale + 1e-9
    assert planes['left_window']['path']['points'][2][1] < 700
    assert planes['right_window']['path']['points'][2][1] < 700
    crossings = {item['form_id'] for item in references['potential_axis_crossings']}
    assert crossings == {'sign_board', 'awning_canopy', 'awning_valance', 'door'}


def test_construction_references_preserve_scope_and_expose_infeasible_coplanarity():
    spec = recorded_cafe()
    scoped = construction_references(spec, ['left_window'])
    assert [item['form_id'] for item in scoped['coplanar_forms']] == ['left_window']
    assert not scoped['planned_connections'] and not scoped['potential_axis_crossings']
    window = next(item for item in spec['composition']['forms'] if item['id'] == 'left_window')
    window['bounds'].update(y=150, height=5)
    reference = construction_references(spec, ['left_window'])['coplanar_forms'][0]
    assert reference['status'] == 'no_coplanar_interval' and reference['path'] is None
    assert reference['near_y_interval'][0] >= reference['near_y_interval'][1]
    assert window['bounds']['y'] == 150 and window['bounds']['height'] == 5
    spec['stage_id'] = 'detail'
    assert construction_references(spec, ['left_window']) is None
    assert 'construction_reference' not in artistic_brief({'drawspec': spec})


def test_recorded_landscape_connections_remain_explicit_without_architectural_projection():
    recorded = json.loads((Path(__file__).parent / 'fixtures/recorded_report_failures.json').read_text(encoding='utf-8'))
    spec = json.loads(recorded['assignment'])['drawspec']
    original = copy.deepcopy(spec)
    references = construction_references(spec, [item['id'] for item in spec['composition']['forms']])
    assert spec == original
    assert not references['coplanar_forms'] and not references['potential_axis_crossings']
    assert {item['relation_id'] for item in references['planned_connections']} == {
        'bridge_to_left_bank', 'bridge_to_right_bank', 'pine_on_left_bank'}
    assert all(item['canvas_points'][0] == item['canvas_points'][1] and item['max_gap'] == 0
               for item in references['planned_connections'])


def test_ambiguous_axes_rotated_forms_and_explicit_parent_choices_keep_the_original_plan():
    spec = recorded_cafe()
    forms = {item['id']: item for item in spec['composition']['forms']}
    forms['left_window']['axis_degrees'] = 12
    rotated = construction_references(spec, ['left_window', 'right_window'])
    assert [item['form_id'] for item in rotated['coplanar_forms']] == ['right_window']
    forms['left_window']['axis_degrees'] = 0
    forms['left_window']['parent_id'] = 'right_facade'
    parent_choice = construction_references(spec, ['left_window', 'right_window'])
    assert [item['form_id'] for item in parent_choice['coplanar_forms']] == ['right_window']
    forms['left_window']['parent_id'] = ''
    axis = copy.deepcopy(forms['near_corner'])
    axis['id'] = 'second_vertical_mass'
    spec['composition']['forms'].append(axis)
    for original, key in (('corner_top', 'second_apex'), ('corner_base', 'second_contact')):
        mark = copy.deepcopy(next(item for item in spec['composition']['landmarks'] if item['id'] == original))
        mark.update(id=key, form_id=axis['id'])
        spec['composition']['landmarks'].append(mark)
    before = copy.deepcopy(spec)
    ambiguous = construction_references(spec, list(forms))
    assert not ambiguous['coplanar_forms'] and not ambiguous['potential_axis_crossings']
    assert spec == before


def test_actual_native_frames_render_from_calculated_anchors_without_changing_the_shared_canvas(tmp_path, native_drawing_child):
    async def check():
        spec = recorded_cafe()
        references = construction_references(spec, ['left_window', 'right_window'])
        store = StudioStore(tmp_path / 'perspective-frames.sqlite3')
        runtime = DrawingRuntime(store, ModelSettings(store))
        session = store.create_session()
        task = store.create_task(session['id'])
        child = native_drawing_child(store, runtime, session, task)
        try:
            output = child.call('draw_paths', {'paths': [item['path'] for item in references['coplanar_forms']]}, 'actual-frames')
            assert output['ok'] and len(output['changed_ids']) == 2
            for native, reference in zip(child.run['draft'], references['coplanar_forms']):
                path = LineString([[native['x'] + x, native['y'] + y] for x, y in native['points']])
                assert path.is_closed
                for anchor in reference['path']['points']:
                    assert path.distance(Point(anchor)) <= 1e-8
            images, ids = await runtime.vision.context(task['id'], child.run['agent_id'], child.run['id'], child.document)
            assert ids and any(block.get('type') == 'image_url' for message in images
                               for block in message.get('content', []) if isinstance(block, dict))
            assert store.get_document(session['document']['id'])['elements'] == []
        finally:
            await runtime.close()
            store.close()
    asyncio.run(check())
