import math

import pytest

from studio.actions import create_native_element
from studio.agents import prepare_drawing_tool, scene_context
from studio.eraser import element_geometry
from studio.pen import interpolate_path
from studio.store import StudioStore


def test_smooth_paths_preserve_anchors_and_create_editable_strokes(tmp_path):
    store = StudioStore(tmp_path / "pen.sqlite3")
    session = store.create_session("人像曲线")
    document = session["document"]
    anchors = [(0, 0), (20, -10), (40, 0), (20, 10)]
    draft, selected, actions, output = prepare_drawing_tool("draw_paths", {
        "origin": [200, 150], "scale": 2, "strokeWidth": 1.2,
        "paths": [{"name": "左眼轮廓", "points": anchors, "closed": True},
                  {"name": "眼睑", "points": [[0, -2], [20, -12], [40, -2]], "opacity": 45}],
    }, document, [], [])
    assert len(draft) == len(actions) == len(output["changed_ids"]) == 2
    for action in actions:
        store.commit_action(document["id"], action["action"], action["expected_elements"])
    stored = store.get_document(document["id"])
    eye = stored["elements"][0]
    absolute = [(round(eye["x"] + x, 3), round(eye["y"] + y, 3)) for x, y in eye["points"]]
    assert all((200 + x * 2, 150 + y * 2) in absolute for x, y in anchors)
    assert absolute[0] == absolute[-1] and len(absolute) > len(anchors)
    assert len(eye["points"]) == len(eye["pressures"]) and eye["type"] == "freedraw"
    _, _, _, inspection = prepare_drawing_tool("inspect_scene", {
        "ids": [eye["id"]], "include_geometry": True,
    }, stored, stored["elements"], selected)
    assert inspection["scene"]["elements"][0]["points"] == eye["points"]
    assert output["objects"][0] == {"id": eye["id"], "name": "左眼轮廓"}
    assert store.history_step(document["id"], "undo")["elements"] == stored["elements"][:1]
    assert store.history_step(document["id"], "redo")["elements"] == stored["elements"]
    reshaped, _, edits, _ = prepare_drawing_tool("reshape_path", {
        "element_id": eye["id"], "points": [[200, 150], [240, 125], [280, 150]],
    }, stored, stored["elements"], selected)
    assert len(edits) == 1 and reshaped[1] == stored["elements"][1]
    assert reshaped[0]["id"] == eye["id"] and reshaped[0]["strokeWidth"] == eye["strokeWidth"]
    assert reshaped[0]["customData"] == eye["customData"]
    store.commit_action(document["id"], edits[0]["action"], edits[0]["expected_elements"])
    assert store.history_step(document["id"], "undo")["elements"] == stored["elements"]
    store.close()


def test_path_batch_validation_preserves_canvas_when_any_stroke_is_invalid():
    document = {"id": "canvas", "version": 0, "elements": [], "selection": []}
    for invalid in ([math.inf, 2], [100001, 2]):
        with pytest.raises(ValueError):
            prepare_drawing_tool("draw_paths", {"paths": [
                {"name": "有效轮廓", "points": [[0, 0], [20, 20]]},
                {"name": "无效轮廓", "points": [[0, 0], invalid]},
            ]}, document, document["elements"], [])
        assert document["elements"] == []
    with pytest.raises(ValueError, match="distinct"):
        interpolate_path([(1, 1), (1, 1)], True, False)
    for color in ("#12345", "#1234567"):
        with pytest.raises(ValueError, match="color"):
            prepare_drawing_tool("draw_paths", {"strokeColor": color, "paths": [
                {"name": "轮廓", "points": [[0, 0], [10, 10]]},
            ]}, document, [], [])
    with pytest.raises(ValueError, match="Transformed"):
        prepare_drawing_tool("draw_paths", {"scale": 1e100, "paths": [
            {"name": "轮廓", "points": [[1, 1], [2, 3]]},
        ]}, document, [], [])
    points = [(index, index % 10) for index in range(1200)]
    _, _, actions, _ = prepare_drawing_tool("create_element", {
        "type": "freedraw", "points": points,
    }, document, [], [])
    assert len(actions[0]["action"]["args"]["points"]) == 1200


def test_region_inspection_finds_older_rotated_strokes_and_combines_filters():
    old = create_native_element({'type': 'freedraw', 'name': '面部参考线', 'points': [[0, 0], [200, 0]]})
    old['angle'] = math.pi / 2
    later = [create_native_element({'type': 'rectangle', 'x': 400 + index * 5, 'y': 300})
             for index in range(75)]
    scene = {'id': 'canvas', 'version': 0, 'elements': [old, *later], 'selection': []}
    assert old['id'] not in {item['id'] for item in scene_context(scene)['elements']}
    _, _, _, output = prepare_drawing_tool('inspect_scene', {
        'region': {'x': 95, 'y': 70, 'width': 10, 'height': 10}, 'include_geometry': True,
    }, scene, scene['elements'], [])
    assert output['scene']['elements'] == scene_context(scene, ids=[old['id']], include_geometry=True)['elements']
    assert scene_context(scene, ids=[old['id']], query='衣袖')['elements'] == []
    assert scene_context(scene, query='面部', region={'x': -5, 'y': -5, 'width': 10, 'height': 10})['elements'] == []
    assert not any(item['isDeleted'] for item in scene['elements'])


def test_reshape_rotated_stroke_uses_final_canvas_coordinates():
    target = create_native_element({'type': 'freedraw', 'name': '面部轮廓', 'points': [[0, 0], [200, 0]]})
    target['angle'] = math.pi / 2
    scene = {'id': 'canvas', 'version': 0, 'elements': [target], 'selection': []}
    draft, _, _, _ = prepare_drawing_tool('reshape_path', {
        'element_id': target['id'], 'points': [[100, -50], [100, 50]], 'smooth': False,
    }, scene, scene['elements'], [])
    geometry = element_geometry(draft[0])
    assert tuple(geometry.coords[0]) == (100, -50) and tuple(geometry.coords[-1]) == (100, 50)
    assert all(point[0] == 100 and -50 <= point[1] <= 50 for point in geometry.coords)
    assert draft[0]['id'] == target['id'] and draft[0]['customData'] == target['customData']
    assert target['angle'] == math.pi / 2
