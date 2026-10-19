import asyncio
import json

import pytest

from studio.actions import create_native_element
from studio.store import StudioStore
from studio.vision import CanvasVision


def test_native_canvas_images_cache_isolate_and_require_current_review(tmp_path):
    async def check():
        store = StudioStore(tmp_path / 'vision.sqlite3')
        vision = CanvasVision(store)
        try:
            session = store.create_session('画布图片')
            other = store.create_session('其他画布')
            task = store.create_task(session['id'])
            caller = f"main:{task['id']}"
            document = store.commit_action(session['document']['id'], {'kind': 'create_element', 'args': {
                'type': 'ellipse', 'x': 100, 'y': 80, 'width': 160, 'height': 120,
                'backgroundColor': '#22c55e', 'name': '绿色椭圆',
            }}, {})['document']
            with pytest.raises(ValueError, match='visual observation'):
                vision.require_review(task['id'], caller, document)
            pictures, ids = await vision.context(task['id'], 'main', caller, document)
            assert len(pictures) == len(ids) == 1
            image_url = pictures[0]['content'][1]['image_url']['url']
            assert image_url.startswith('data:image/png;base64,')
            assert vision.image(session['id'], ids[0]).startswith(b'\x89PNG\r\n\x1a\n')
            with pytest.raises(KeyError, match='session'):
                vision.image(other['id'], ids[0])
            with pytest.raises(ValueError, match='visual observation'):
                vision.require_review(task['id'], caller, document)
            vision.delivered(task['id'], caller, ids)
            vision.require_review(task['id'], caller, document)
            _, repeated = await vision.context(task['id'], 'main', caller, document)
            assert repeated == ids
            crop = await vision.observe(task['id'], 'main', caller, document, {
                'x': 100, 'y': 80, 'width': 80, 'height': 120,
            })
            assert crop['width'] == 853 and crop['height'] == 1280
            assert crop['scene_bounds'] == {'x': 100, 'y': 80, 'width': 80, 'height': 120}
            closeup, _ = await vision.context(task['id'], 'main', caller, document)
            assert len(closeup) == 2
            element = create_native_element({'type': 'rectangle', 'x': 400, 'y': 100, 'name': '私有矩形'})
            private = {**document, 'elements': document['elements'] + [element]}
            child = store.agents.create_agent(session['id'], task['id'], 'drawing-child', '检查草稿', 'Artist', [])
            own, own_ids = await vision.context(task['id'], child['agent_id'], child['run_id'], private)
            assert own[0]['content'][1]['image_url']['url'] != image_url
            assert own_ids[0] not in ids
            with pytest.raises(ValueError, match='another Agent'):
                vision.delivered(task['id'], caller, own_ids)
            with pytest.raises(ValueError, match='visual observation'):
                vision.require_review(task['id'], caller, private)
            latest, latest_ids = await vision.context(task['id'], 'main', caller, private)
            assert len(latest) == 1 and latest_ids[0] not in ids
            logs = json.dumps(store.tool_logs(task['id']))
            assert 'base64' not in logs and 'pixels_per_unit' in logs
            store.update_task(task['id'], 'completed')
            store.delete_session(session['id'])
            assert store.connection.execute('SELECT count(*) FROM canvas_observations').fetchone()[0] == 0
            with pytest.raises(KeyError, match='session'):
                vision.image(session['id'], ids[0])
            assert store.get_session(other['id']) == other
        finally:
            await vision.close()
            store.close()
    asyncio.run(check())
