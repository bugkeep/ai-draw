import argparse
import json
import os
import time
from pathlib import Path

from fastapi.testclient import TestClient

from server.app import create_app
from studio.actions import element_fingerprint


def execute(client, session_id, prompt):
    response = client.post(f'/api/studio/sessions/{session_id}/messages', json={'message': prompt})
    response.raise_for_status()
    task_id = response.json()['id']
    try:
        while True:
            response = client.get(f'/api/studio/tasks/{task_id}')
            response.raise_for_status()
            task = response.json()
            if task['status'] in {'completed', 'partial', 'failed', 'cancelled'}:
                return task
            time.sleep(0.05)
    except KeyboardInterrupt:
        client.post(f'/api/studio/tasks/{task_id}/cancel').raise_for_status()
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--database', type=Path, default=Path('runs/memory-evaluation.sqlite3'))
    parser.add_argument('--output', type=Path, default=Path('runs/memory-evaluation.json'))
    args = parser.parse_args()
    if args.database.exists():
        raise ValueError('Use a new independent evaluation database')
    os.environ['DRAW_AGENT_DB'] = str(args.database.resolve())
    app = create_app()
    with TestClient(app) as client:
        if not client.get('/api/studio/health').json()['provider_configured']:
            raise ValueError('Configure a real model before evaluation')
        session = client.post('/api/studio/sessions', json={'title': '长期会话记忆与检索'}).json()
        for index in range(80):
            name = '纪念点' if index == 0 else f'背景点{index}'
            task = execute(client, session['id'], f'画一个蓝色圆形，名字叫{name}。')
            if task['status'] != 'completed':
                raise RuntimeError(task['error'])
        store = app.state.studio_store
        before = store.get_document(session['document']['id'])
        target = before['elements'][0]
        print(json.dumps({'baseline_objects': len(before['elements']), 'session_id': session['id']}, ensure_ascii=False), flush=True)
        first = execute(client, session['id'], '使用 search_memory 找回最早提到“纪念点”的指令，使用 recall_memory '
            '取回它当前的几何信息。把它改成红色（#e03131），并向右移动20像素，其他图形保持原样。'
            '用 compact_context 保存目标、原对象编号、已完成和待办事项。完成前检查实际图片。')
        after_first = store.get_document(before['id'])
        first_notes = store.connection.execute('SELECT m.payload FROM agent_working_memory w JOIN agent_memories m '
            'ON m.id=w.memory_ref WHERE w.session_id=? AND w.actor_id=?', (session['id'], 'main')).fetchone()
        if first_notes is None:
            raise RuntimeError('The real Agent did not save working memory')
        saved_notes = json.loads(first_notes['payload'])
    reopened = create_app()
    with TestClient(reopened) as client:
        store = reopened.state.studio_store
        row = store.connection.execute('SELECT m.payload FROM agent_working_memory w JOIN agent_memories m '
            'ON m.id=w.memory_ref WHERE w.session_id=? AND w.actor_id=?', (session['id'], 'main')).fetchone()
        retained = json.loads(row['payload']) == saved_notes
        second = execute(client, session['id'], '根据上轮工作记忆，使用记忆检索取回那个“纪念点”的信息，把同一个对象再向下移动15像素。'
            '保留它的名称、尺寸和红色，其他图形保持原样。用 compact_context 更新本轮目标、已完成内容和待办事项。'
            '完成前查看实际图片。')
        final = store.get_document(before['id'])
        updated = next(item for item in final['elements'] if item['id'] == target['id'])
        first_object = next(item for item in after_first['elements'] if item['id'] == target['id'])
        logs = [entry for task in (first, second) for entry in store.tool_logs(task['id'])]
        calls = [entry['payload']['name'] for entry in logs if entry['kind'] == 'function_call']
        errors = [entry['payload']['call_id'] for entry in logs if entry['kind'] == 'function_call_output'
                  and json.loads(entry['payload']['output']).get('ok') is False]
        delivered = {call_id for entry in logs if entry['kind'] == 'model_context'
                     for call_id in entry['payload'].get('tool_error_ids', [])}
        notes = store.connection.execute('SELECT w.revision,m.task_id,m.payload FROM agent_working_memory w '
            'JOIN agent_memories m ON m.id=w.memory_ref WHERE w.session_id=? AND w.actor_id=?', (session['id'], 'main')).fetchone()
        unchanged = {item['id']: item for item in final['elements'] if item['id'] != target['id']}
        checks = {
            'both_model_tasks_completed': first['status'] == second['status'] == 'completed',
            'search_tool_used': calls.count('search_memory') >= 2,
            'precise_recall_used': calls.count('recall_memory') >= 2,
            'working_memory_compacted': calls.count('compact_context') >= 2,
            'working_memory_survives_restart': retained,
            'old_original_object_edited': first_object['x'] == target['x'] + 20 and updated['y'] == target['y'] + 15,
            'red_color_preserved': first_object['backgroundColor'] == updated['backgroundColor'] == '#e03131',
            'identity_name_and_size_preserved': all(updated[field] == target[field] for field in ('id', 'customData', 'width', 'height')),
            'other_79_objects_preserved': len(unchanged) == 79 and all(element_fingerprint(item) ==
                element_fingerprint(unchanged[item['id']]) for item in before['elements'][1:]),
            'latest_notes_belong_to_second_task': notes['task_id'] == second['id'] and notes['revision'] >= 2,
            'errors_reach_context': set(errors) <= delivered,
            'actual_images_delivered': sum(len(entry['payload'].get('image_observation_ids', []))
                for entry in logs if entry['kind'] == 'model_context') >= 4,
        }
        result = {'session_id': session['id'], 'database': str(args.database), 'tasks': [first, second],
                  'checks': checks, 'tool_calls': {name: calls.count(name) for name in sorted(set(calls))},
                  'tool_error_count': len(errors), 'passed': all(checks.values())}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(result, ensure_ascii=False), flush=True)
        if not result['passed']:
            raise SystemExit(1)


if __name__ == '__main__':
    main()
