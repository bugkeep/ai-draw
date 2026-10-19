import argparse
import json
import os
import time
from collections import Counter
from pathlib import Path

from fastapi.testclient import TestClient

from server.app import create_app
from studio.actions import create_native_element, element_fingerprint


def wait_task(client, session_id, prompt):
    response = client.post(f'/api/studio/sessions/{session_id}/messages', json={'message': prompt})
    response.raise_for_status()
    task_id = response.json()['id']
    print(json.dumps({'task_id': task_id, 'prompt': prompt}, ensure_ascii=False), flush=True)
    try:
        while True:
            task = client.get(f'/api/studio/tasks/{task_id}').json()
            if task['status'] in {'completed', 'partial', 'failed', 'cancelled'}:
                return task
            time.sleep(0.5)
    except KeyboardInterrupt:
        client.post(f'/api/studio/tasks/{task_id}/cancel').raise_for_status()
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--database', type=Path, default=Path('runs/visual-edit.sqlite3'))
    parser.add_argument('--output', type=Path, default=Path('runs/visual-edit.json'))
    args = parser.parse_args()
    if args.database.resolve() == Path('runs/studio.sqlite3').resolve():
        raise ValueError('Use a separate evaluation database')
    os.environ['DRAW_AGENT_DB'] = str(args.database.resolve())
    app = create_app()
    with TestClient(app) as client:
        store = app.state.studio_store
        session = client.post('/api/studio/sessions', json={'title': '看图与擦除检查'}).json()
        specifications = [
            {'type': 'ellipse', 'x': 100, 'y': 80, 'width': 300, 'height': 380, 'strokeWidth': 0.7},
            {'type': 'ellipse', 'x': 155, 'y': 205, 'width': 60, 'height': 34, 'strokeWidth': 0.7},
            {'type': 'ellipse', 'x': 177, 'y': 213, 'width': 16, 'height': 18, 'backgroundColor': '#111111'},
            {'type': 'ellipse', 'x': 285, 'y': 205, 'width': 60, 'height': 34, 'strokeWidth': 0.7},
            {'type': 'ellipse', 'x': 307, 'y': 213, 'width': 16, 'height': 18, 'backgroundColor': '#111111'},
            {'type': 'freedraw', 'points': [[252, 230], [242, 280], [260, 282]], 'strokeWidth': 0.4},
            {'type': 'freedraw', 'points': [[195, 365], [220, 378], [250, 383], [280, 378], [305, 365]], 'strokeWidth': 0.4},
            {'type': 'ellipse', 'x': 220, 'y': 310, 'width': 60, 'height': 34, 'strokeWidth': 0.7},
            {'type': 'ellipse', 'x': 242, 'y': 318, 'width': 16, 'height': 18, 'backgroundColor': '#111111'},
        ]
        elements = [create_native_element({**spec, 'name': f'形状{index + 1}'})
                    for index, spec in enumerate(specifications)]
        document_id = session['document']['id']
        store.save_snapshot(document_id, 0, elements, [])
        first = wait_task(client, session['id'], '查看这张人脸图片，使用橡皮擦去掉多余的那只眼睛和它的瞳孔，保留正常的两只眼睛、鼻子、嘴和脸部轮廓；完成后检查实际图片。')
        after_first = store.get_document(document_id)
        active = {item['id']: item for item in after_first['elements'] if not item.get('isDeleted')}
        checks = {'visual_eye_correction_completed': first['status'] == 'completed',
                  'extra_eye_erased': all(item['id'] not in active for item in elements[-2:]),
                  'other_face_parts_preserved': all(item['id'] in active and element_fingerprint(item) ==
                      element_fingerprint(active[item['id']]) for item in elements[:-2])}
        line = store.commit_action(document_id, {'kind': 'create_element', 'args': {
            'type': 'freedraw', 'points': [[20, 300], [480, 300]], 'strokeWidth': 0.5, 'name': '形状10',
        }}, {})['document']['elements'][-1]
        before_second = store.get_document(document_id)
        second = wait_task(client, session['id'], '查看图片，用橡皮擦局部擦掉穿过脸部的那条横线，只去掉脸部范围内的一段，保留横线左右两端，其他图形保持原样。完成后重新看图检查。')
        after_second = store.get_document(document_id)
        fragments = [item for item in after_second['elements'] if not item.get('isDeleted') and
                     (item['id'] == line['id'] or item.get('customData', {}).get('sourceElementId') == line['id'])]
        checks['partial_erasing_completed'] = second['status'] == 'completed'
        checks['both_line_ends_preserved'] = len(fragments) >= 2 and any(item['x'] < 50 for item in fragments) and any(
            item['x'] + item['width'] > 450 for item in fragments)
        checks['face_interior_cleared'] = not any(item['x'] < 250 < item['x'] + item['width'] for item in fragments)
        unrelated = {item['id']: item for item in after_second['elements'] if item['id'] != line['id'] and
                     item.get('customData', {}).get('sourceElementId') != line['id']}
        checks['segment_erase_preserves_other_objects'] = all(item['id'] in unrelated and
            element_fingerprint(item) == element_fingerprint(unrelated[item['id']])
            for item in before_second['elements'] if item['id'] != line['id'])
        logs = [entry for task in (first, second) for entry in store.tool_logs(task['id'])]
        calls = Counter(entry['payload']['name'] for entry in logs if entry['kind'] == 'function_call')
        contexts = [entry['payload'] for entry in logs if entry['kind'] == 'model_context']
        checks['eraser_tool_used'] = calls['erase_strokes'] >= 2
        checks['actual_images_in_model_context'] = sum(len(item.get('image_observation_ids', [])) for item in contexts) >= 4
        previous = store.get_history(document_id, 'undo')[-1]
        checks['undo'] = store.history_step(document_id, 'undo')['elements'] == previous['elements']
        checks['redo'] = store.history_step(document_id, 'redo')['elements'] == after_second['elements']
        observations = [dict(row) for row in store.connection.execute(
            'SELECT id,metadata,png FROM canvas_observations WHERE session_id=? ORDER BY created_at', (session['id'],))]
        for index, image in enumerate(observations):
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.with_name(f'{args.output.stem}-view-{index + 1}.png').write_bytes(image['png'])
        result = {'session_id': session['id'], 'tasks': [first, second], 'checks': checks,
                  'tool_calls': dict(calls), 'observation_count': len(observations),
                  'tool_error_count': sum(entry['kind'] == 'function_call_output' and
                      json.loads(entry['payload']['output']).get('ok') is False for entry in logs),
                  'passed': all(checks.values())}
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(result, ensure_ascii=False), flush=True)
        if not result['passed']:
            raise SystemExit(1)


if __name__ == '__main__':
    main()
