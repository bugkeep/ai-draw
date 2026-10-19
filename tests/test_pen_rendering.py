import asyncio
import io
import math

from PIL import Image
from shapely import LineString

from studio.actions import create_native_element
from studio.agents import prepare_drawing_tool
from studio.renderer import CanvasRenderer
from studio.store import StudioStore


def ink_near(rendered, point, radius=2):
    bounds = rendered['scene_bounds']
    with Image.open(io.BytesIO(rendered['png'])) as image:
        x = (point[0] - bounds['x']) * rendered['pixels_per_unit']['x']
        y = (point[1] - bounds['y']) * rendered['pixels_per_unit']['y']
        rx = radius * rendered['pixels_per_unit']['x']
        ry = radius * rendered['pixels_per_unit']['y']
        crop = image.convert('L').crop((max(0, math.floor(x - rx)), max(0, math.floor(y - ry)),
            min(image.width, math.ceil(x + rx)), min(image.height, math.ceil(y + ry))))
        return crop.getextrema()[0] < 160


def test_native_sampling_preserves_closed_geometry_and_real_export_pixels_after_reopen_and_undo(tmp_path):
    async def check():
        database = tmp_path / 'pen-rendering.sqlite3'
        store = StudioStore(database)
        renderer = CanvasRenderer()
        anchors = [[40, 40], [340, 40], [340, 240], [40, 240], [40, 40]]
        frame = {'x': 0, 'y': 0, 'width': 400, 'height': 280}
        try:
            session = store.create_session()
            document = session['document']
            # This sparse representation reproduces the real Excalidraw export
            # defect. It is an actual native object, not a mocked model result.
            sparse = create_native_element({'type': 'freedraw', 'points': anchors, 'strokeWidth': 0.4})
            old_image = await renderer.render({'elements': [sparse]}, frame)
            _, _, actions, _ = prepare_drawing_tool('draw_paths', {'paths': [{
                'name': '闭合轮廓', 'points': anchors, 'smooth': False, 'closed': True}]}, document, [], [])
            final = store.commit_action(document['id'], actions[0]['action'], {})['document']
            element = final['elements'][0]
            actual = [(element['x'] + x, element['y'] + y) for x, y in element['points']]
            assert LineString(actual).hausdorff_distance(LineString(anchors)) < 1e-10
            samples = [(first[0] + (last[0] - first[0]) * fraction,
                        first[1] + (last[1] - first[1]) * fraction)
                       for first, last in zip(anchors, anchors[1:]) for fraction in (0.15, 0.5, 0.85)]
            assert not all(ink_near(old_image, point) for point in samples)
            current_image = await renderer.render(final, frame)
            assert all(ink_near(current_image, point) for point in samples)
            assert store.history_step(document['id'], 'undo')['elements'] == []
            assert store.history_step(document['id'], 'redo')['elements'] == final['elements']
            store.close()
            store = StudioStore(database)
            reopened = store.get_document(document['id'])
            restored_image = await renderer.render(reopened, frame)
            assert restored_image['png'] == current_image['png']
        finally:
            await renderer.close()
            store.close()

    asyncio.run(check())
