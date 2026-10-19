import argparse
import json
import os
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from server.app import create_app
from studio.actions import element_fingerprint
from .evaluate_memory import execute


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-database', type=Path, required=True)
    parser.add_argument('--reference-id', required=True)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.database.exists():
        raise ValueError('Use a new independent evaluation database')
    with sqlite3.connect(f'{args.source_database.resolve().as_uri()}?mode=ro', uri=True) as source:
        source.row_factory = sqlite3.Row
        row = source.execute('SELECT * FROM reference_images WHERE id=?', (args.reference_id,)).fetchone()
        if row is None:
            raise ValueError('Choose a real previously downloaded reference image')
        reference = dict(row)
    os.environ['DRAW_AGENT_DB'] = str(args.database.resolve())
    app = create_app()
    with TestClient(app) as client:
        store = app.state.studio_store
        session = store.create_session('实际参考轮廓与子 Agent 连续编辑')
        document_id = session['document']['id']
        baseline = store.commit_action(document_id, {'kind': 'create_element', 'args': {
            'type': 'ellipse', 'name': '保留的标记', 'x': -200, 'y': -200, 'width': 30, 'height': 30}}, {})['document']
        protected = baseline['elements'][0]
        import_task = store.create_task(session['id'])
        metadata = json.loads(reference['metadata'])
        saved = app.state.drawing_runtime.references.save_image(import_task['id'], 'main', {
            'title': metadata['title'], 'url': metadata['url'], 'image': metadata['image_url'],
        }, reference['original'], (reference['png'], metadata))
        store.update_task(import_task['id'], 'completed')
        image_id = saved['reference_id']
        print(json.dumps({'session_id': session['id'], 'reference_id': image_id}), flush=True)
        first = execute(client, session['id'], f'已有参考图片 {image_id}。创建一个子 Agent，名称为“轮廓助手”，'
            '让它调用 view_reference_image 查看该完整参考图片，检查实际图片，然后用 trace_reference '
            '提取第一批3条轮廓（limit=3，offset=0），mode=edges，min_length=15，name=参考轮廓，'
            '整张源图像的共同 target 为 x=0,y=0,width=500,height=600。'
            '这次任务只需要这3条，其他路径留待后续指令。由主 Agent 提交结果，完成前查看实际画布，保留原有标记。')
        after_first = store.get_document(document_id)
        strokes = [item for item in after_first['elements'] if item['id'] != protected['id'] and not item.get('isDeleted')]
        if len(strokes) != 3:
            raise RuntimeError(f'The real model did not prepare exactly three native contours: {first}')
        target = strokes[0]
        second = execute(client, session['id'], f'继续使用原来的“轮廓助手”，通过 send_input 安排它修改编号 '
            f'{target["id"]} 的参考轮廓。使用 search_memory 找到它，再使用 recall_memory 读取当前几何，'
            '把同一条笔画向右移动20像素，其他对象保持原样。主 Agent 提交这次编辑并检查实际画布。')
        final = store.get_document(document_id)
        final_by_id = {item['id']: item for item in final['elements'] if not item.get('isDeleted')}
        logs = [entry for task in (first, second) for entry in store.tool_logs(task['id'])]
        calls = [entry for entry in logs if entry['kind'] == 'function_call']
        errors = [entry['payload']['call_id'] for entry in logs if entry['kind'] == 'function_call_output'
                  and json.loads(entry['payload']['output']).get('ok') is False]
        delivered = {call_id for entry in logs if entry['kind'] == 'model_context'
                     for call_id in entry['payload'].get('tool_error_ids', [])}
        actors = {entry['agent_id'] for entry in calls if entry['payload']['name'] == 'trace_reference'}
        native = {'type': 'excalidraw', 'version': 2, 'source': 'draw-agent-studio',
                  'elements': final['elements'], 'appState': {'viewBackgroundColor': '#ffffff'}, 'files': {}}
        undid = client.post(f'/api/studio/documents/{document_id}/history', json={'direction': 'undo'}).json()
        redid = client.post(f'/api/studio/documents/{document_id}/history', json={'direction': 'redo'}).json()
        checks = {
            'both_actual_model_tasks_completed': first['status'] == second['status'] == 'completed',
            'child_prepares_native_contours': actors and 'main' not in actors and all(item['type'] == 'freedraw' for item in strokes),
            'only_one_child_created': len(store.agents.list_agents(session['id'])) == 1,
            'same_child_receives_followup': any(entry['payload']['name'] == 'send_input' for entry in calls),
            'main_submits_both_results': sum(entry['agent_id'] == 'main' and entry['payload']['name'] == 'apply_agent_result' for entry in calls) >= 2,
            'same_object_moves_exactly': target['id'] in final_by_id and final_by_id[target['id']]['x'] == target['x'] + 20
                and final_by_id[target['id']]['y'] == target['y'],
            'other_original_objects_preserved': len(final_by_id) == 4 and all(
                element_fingerprint(item) == element_fingerprint(final_by_id[item['id']])
                for item in after_first['elements'] if item['id'] != target['id']),
            'reference_pixels_enter_child_context': any(entry['agent_id'] in actors and
                entry['payload'].get('reference_image_view_ids') for entry in logs if entry['kind'] == 'model_context'),
            'current_canvas_enters_context': any(entry['payload'].get('image_observation_ids') for entry in logs if entry['kind'] == 'model_context'),
            'tool_errors_reach_context': set(errors) <= delivered,
            'undo_restores_original_position': next(item for item in undid['elements'] if item['id'] == target['id'])['x'] == target['x'],
            'redo_restores_edit': redid['elements'] == final['elements'],
        }
        result = {'session_id': session['id'], 'reference_id': image_id, 'tasks': [first, second],
                  'checks': {key: bool(value) for key, value in checks.items()}, 'tool_error_count': len(errors),
                  'passed': all(checks.values()), 'scope': 'real reference tracing and continuous editing; not whole-art visual acceptance'}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        args.output.with_suffix('.excalidraw').write_text(json.dumps(native, ensure_ascii=False), encoding='utf-8')
        print(json.dumps(result), flush=True)
        if not result['passed']:
            raise SystemExit(1)


if __name__ == '__main__':
    main()
