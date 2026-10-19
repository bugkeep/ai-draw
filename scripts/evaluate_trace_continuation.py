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
        reference = source.execute('SELECT * FROM reference_images WHERE id=?', (args.reference_id,)).fetchone()
        if reference is None:
            raise ValueError('Choose an actual saved reference image')
        reference = dict(reference)
    os.environ['DRAW_AGENT_DB'] = str(args.database.resolve())
    app = create_app()
    with TestClient(app) as client:
        store = app.state.studio_store
        runtime = app.state.drawing_runtime
        session = store.create_session('实际模型跨 Agent 继续轮廓分页')
        document_id = session['document']['id']
        baseline = store.commit_action(document_id, {'kind': 'create_element', 'args': {
            'type': 'ellipse', 'name': '保留标记', 'x': -100, 'y': -100, 'width': 20, 'height': 20}}, {})['document']
        protected = baseline['elements'][0]
        imported = store.create_task(session['id'])
        metadata = json.loads(reference['metadata'])
        saved = runtime.references.save_image(imported['id'], 'reference-import', {
            'title': metadata['title'], 'url': metadata['url'], 'image': metadata['image_url'],
        }, reference['original'], (reference['png'], metadata))
        store.update_task(imported['id'], 'completed')
        task = execute(client, session['id'], f'已有参考图 {saved["reference_id"]}。主 Agent 先调用 '
            'view_reference_image 查看完整源图，然后调用 trace_reference 创建计划并提取前2条轮廓，'
            'mode=edges，min_length=15，name=统一计划，target 为 x=0,y=0,width=400,height=500。'
            '接着创建一个子 Agent，把返回的 trace_plan_id 和 next_page 交给它。'
            '它必须先查看完整参考图，再调用 continue_reference_trace 从同一计划继续提取2条；'
            '续页只传 trace_plan_id、offset 和 limit=2。由主 Agent 提交子 Agent 的结果。'
            '本轮只需要这4条，检查实际画布后完成，保留原有标记。')
        document = store.get_document(document_id)
        strokes = [item for item in document['elements'] if not item.get('isDeleted') and item['id'] != protected['id']]
        logs = store.tool_logs(task['id'])
        calls = [entry for entry in logs if entry['kind'] == 'function_call']
        outputs = {(entry['agent_id'], entry['payload']['call_id']): json.loads(entry['payload']['output'])
                   for entry in logs if entry['kind'] == 'function_call_output'}
        parent = next((entry for entry in calls if entry['agent_id'] == 'main'
                       and entry['payload']['name'] == 'trace_reference'
                       and outputs.get(('main', entry['payload']['call_id']), {}).get('ok')), None)
        parent_output = outputs[('main', parent['payload']['call_id'])] if parent else {}
        continuation = [entry for entry in calls if entry['agent_id'] != 'main'
                        and entry['payload']['name'] == 'continue_reference_trace'
                        and outputs.get((entry['agent_id'], entry['payload']['call_id']), {}).get('ok')]
        child_outputs = [outputs[(entry['agent_id'], entry['payload']['call_id'])] for entry in continuation]
        plan_id = parent_output.get('trace_plan_id')
        parent_ids = set(parent_output.get('changed_ids', []))
        history = store.get_history(document_id, 'undo')
        original = next((state['elements'] for state in history
                         if {item['id'] for item in state['elements']} == parent_ids | {protected['id']}), [])
        errors = [(entry['agent_id'], entry['payload']['call_id']) for entry in logs if entry['kind'] == 'function_call_output'
                  and json.loads(entry['payload']['output']).get('ok') is False]
        delivered = {(entry['agent_id'], call_id) for entry in logs if entry['kind'] == 'model_context'
                     for call_id in entry['payload'].get('tool_error_ids', [])}
        checks = {
            'actual_model_task_completed': task['status'] == 'completed',
            'main_starts_saved_plan': bool(plan_id),
            'child_continues_exact_parent_plan': bool(continuation) and all(
                json.loads(entry['payload']['arguments']).get('trace_plan_id') == plan_id for entry in continuation),
            'main_submits_child_result': any(entry['agent_id'] == 'main' and entry['payload']['name'] == 'apply_agent_result' for entry in calls),
            'immutable_continuation_metadata': bool(child_outputs) and all(
                all(output[key] == parent_output[key] for key in ('trace_id', 'trace_plan_id', 'source_frame', 'source_region', 'target', 'mapping'))
                and output['offset'] == 2 and output['returned_paths'] == 2 for output in child_outputs),
            'four_ordered_native_strokes': len(strokes) == 4 and all(item['type'] == 'freedraw' for item in strokes)
                and [item.get('customData', {}).get('name') for item in strokes] == [f'统一计划/轮廓-{index:04d}' for index in range(1, 5)],
            'main_strokes_preserved_after_child_commit': len(original) == 3 and all(
                any(item['id'] == before['id'] and element_fingerprint(item) == element_fingerprint(before)
                    for item in document['elements']) for before in original),
            'other_original_objects_preserved': any(element_fingerprint(item) == element_fingerprint(protected)
                for item in document['elements'] if item['id'] == protected['id']),
            'reference_pixels_enter_child_context': any(entry['agent_id'] != 'main' and entry['kind'] == 'model_context'
                and entry['payload'].get('reference_image_view_ids') for entry in logs),
            'actual_canvas_inspected': any(entry['agent_id'] == 'main' and entry['kind'] == 'model_context'
                and entry['payload'].get('image_observation_ids') for entry in logs),
            'tool_errors_reach_context': set(errors) <= delivered,
        }
        result = {'session_id': session['id'], 'task': task, 'trace_plan_id': plan_id,
            'checks': checks, 'tool_error_count': len(errors), 'passed': all(checks.values()),
            'scope': 'actual model parent/child continuation of one immutable trace plan; not whole-art visual acceptance'}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(result), flush=True)
        if not result['passed']:
            raise SystemExit(1)


if __name__ == '__main__':
    main()
