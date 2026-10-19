import argparse
import json
import os
from pathlib import Path

from fastapi.testclient import TestClient

from server.app import create_app
from studio.actions import create_native_element, element_fingerprint
from .evaluate_memory import execute


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.database.exists():
        raise ValueError('Use a new independent evaluation database')
    os.environ['DRAW_AGENT_DB'] = str(args.database.resolve())
    app = create_app()
    with TestClient(app) as client:
        if not client.get('/api/studio/health').json()['provider_configured']:
            raise ValueError('Configure a real model before evaluation')
        store = app.state.studio_store
        session = store.create_session('真实模型分页定位与子 Agent 编辑')
        document_id = session['document']['id']
        elements = [create_native_element({'type': 'freedraw', 'name': '练习轮廓',
            'x': 30, 'y': 20 + index * 12, 'points': [[0, 0], [20, -3], [40, 2], [60, 0]],
            'strokeColor': '#f03e3e' if index == 13 else '#868e96', 'strokeWidth': 2}) for index in range(21)]
        before = store.save_snapshot(document_id, 0, elements, [])
        target = elements[13]
        task = execute(client, session['id'],
            '请创建一个子 Agent 定位并修改这组练习轮廓。子 Agent 使用 inspect_scene 查询 '
            'x=0,y=0,width=160,height=320 的区域，每页 limit=6，按返回的 next_page 继续查找唯一的红色笔画 '
            '(strokeColor=#f03e3e)，直到找到当前对象编号。不要通过名称猜测编号，也不要改其他笔画。'
            '将该红色笔画向右移动17、向下移动23，查看草稿图片后结束这次输入。由主 Agent 提交子 Agent 结果，'
            '检查画布并完成本轮。这是一次定位与移动，保持全部笔画及原有样式。')
        final = store.get_document(document_id)
        by_id = {item['id']: item for item in final['elements']}
        logs = store.tool_logs(task['id'])
        calls = [entry for entry in logs if entry['kind'] == 'function_call']
        outputs = {(entry['agent_id'], entry['payload']['call_id']): json.loads(entry['payload']['output'])
                   for entry in logs if entry['kind'] == 'function_call_output'}
        pages = [(entry, outputs[(entry['agent_id'], entry['payload']['call_id'])]['scene'])
            for entry in calls if entry['agent_id'] != 'main' and entry['payload']['name'] == 'inspect_scene'
            and outputs.get((entry['agent_id'], entry['payload']['call_id']), {}).get('ok')]
        located = any(target['id'] in {item['id'] for item in page['elements']} and page['offset'] >= 12
                      for _, page in pages)
        continued = [(entry, page) for entry, page in pages if page['offset'] > 0]
        context_pages = []
        for entry, page in pages:
            row = store.connection.execute('SELECT summary FROM agent_memories WHERE caller_id=? AND call_id=?',
                (entry['payload']['run_id'], entry['payload']['call_id'])).fetchone()
            context_pages.append(bool(row) and [item['id'] for item in json.loads(row['summary'])['scene']['elements']]
                                 == [item['id'] for item in page['elements']])
        errors = [(entry['agent_id'], entry['payload']['call_id']) for entry in logs
                  if entry['kind'] == 'function_call_output' and json.loads(entry['payload']['output']).get('ok') is False]
        delivered = {(entry['agent_id'], call_id) for entry in logs if entry['kind'] == 'model_context'
                     for call_id in entry['payload'].get('tool_error_ids', [])}
        undone = store.history_step(document_id, 'undo')
        redone = store.history_step(document_id, 'redo')
        checks = {
            'actual_model_task_completed': task['status'] == 'completed',
            'main_spawns_child': any(entry['agent_id'] == 'main' and entry['payload']['name'] == 'spawn_agent' for entry in calls),
            'child_locates_object_beyond_first_pages': located,
            'continuation_uses_scene_state': bool(continued) and all(
                json.loads(entry['payload']['arguments']).get('expected_scene_state') == page['scene_state']
                for entry, page in continued),
            'all_requested_ids_reach_structured_context': bool(context_pages) and all(context_pages),
            'main_commits_child_result': any(entry['agent_id'] == 'main' and entry['payload']['name'] == 'apply_agent_result' for entry in calls),
            'exact_same_object_moves': target['id'] in by_id and by_id[target['id']]['x'] == target['x'] + 17
                and by_id[target['id']]['y'] == target['y'] + 23,
            'other_native_objects_preserved': set(by_id) == {item['id'] for item in elements} and all(
                element_fingerprint(by_id[item['id']]) == element_fingerprint(item) for item in elements if item['id'] != target['id']),
            'actual_draft_pixels_enter_child_context': any(entry['agent_id'] != 'main' and entry['kind'] == 'model_context'
                and entry['payload'].get('image_observation_ids') for entry in logs),
            'tool_errors_reach_context': set(errors) <= delivered,
            'undo_restores_original_scene': undone['elements'] == before['elements'],
            'redo_restores_edited_scene': redone['elements'] == final['elements'],
        }
        result = {'session_id': session['id'], 'task': task,
                  'pages': [{'actor_id': entry['agent_id'], 'offset': page['offset'], 'returned_count': page['returned_count'],
                             'matching_count': page['matching_count'], 'scene_state': page['scene_state']} for entry, page in pages],
                  'checks': checks, 'tool_error_count': len(errors), 'passed': all(checks.values()),
                  'scope': 'real model child pagination and exact-object editing; not whole-art visual acceptance'}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(result, ensure_ascii=False), flush=True)
        if not result['passed']:
            raise SystemExit(1)


if __name__ == '__main__':
    main()
