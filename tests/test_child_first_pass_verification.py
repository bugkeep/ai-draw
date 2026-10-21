import asyncio
import json

import pytest

from studio.child_execution import verification_checkpoint
from studio.runtime import DrawingRuntime
from studio.settings import ModelSettings
from studio.store import StudioStore


@pytest.mark.parametrize('operation', ['draw_paths', 'erase_strokes', 'delete_elements'])
def test_first_whole_view_verifies_actual_construction_and_erasure(tmp_path, native_drawing_child, operation):
    async def check():
        store = StudioStore(tmp_path / 'first-pass.sqlite3')
        runtime = DrawingRuntime(store, ModelSettings(store))
        session = store.create_session()
        task = store.create_task(session['id'])
        child = native_drawing_child(store, runtime, session, task)
        try:
            assert verification_checkpoint(store, child.run, runtime.children.draft_reviews.follow_up(child.run)) is None
            made = child.call('draw_paths', {'paths': [
                {'name': '当前轮廓', 'points': [[0, 0], [100, 0]], 'smooth': False},
                {'name': '保留轮廓', 'points': [[0, 80], [100, 80]], 'smooth': False}]}, 'construct-first-pass')
            assert made['ok']
            if operation != 'draw_paths':
                arguments = {'ids': [made['changed_ids'][0]]}
                if operation == 'erase_strokes':
                    arguments.update(points=[[50, 0]], radius=8)
                changed = child.call(operation, arguments, 'correct-first-pass')
                assert changed['ok'] and changed['action_count'] > 0
            reason = runtime.children.draft_reviews.follow_up(child.run)
            assert reason == {'first_changed_draft': True}
            assert verification_checkpoint(store, child.run, reason) is None
            for call_id, region in [('focused-view', {'x': 0, 'y': 0, 'width': 100, 'height': 100}),
                                    ('whole-first-pass', None)]:
                assert store.agents.begin_call(task['id'], child.run['id'], call_id, 'view_canvas',
                    json.dumps({'region': region} if region else {})) is None
                observed = await runtime.vision.observe(task['id'], child.run['agent_id'], child.run['id'],
                    child.document, region, remember_region=True)
                store.agents.finish_call(child.run['id'], call_id, observed, child.run)
                before = store.connection.total_changes
                checkpoint = verification_checkpoint(store, child.run,
                    runtime.children.draft_reviews.follow_up(child.run))
                assert store.connection.total_changes == before
                expected = ({'first_pass_verification': {'call_id': call_id}}
                    if region is None and operation != 'delete_elements' else None)
                assert checkpoint == expected
            assert not store.connection.execute('SELECT 1 FROM draft_reviews').fetchone()
        finally:
            await runtime.close()
            store.close()
    asyncio.run(check())
