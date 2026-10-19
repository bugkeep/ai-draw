"""Use real model image requests to test rejection and approval at the runtime completion boundary."""

import argparse
import asyncio
import hashlib
import json
import time
from pathlib import Path

from dotenv import load_dotenv

from studio.actions import create_native_element, element_fingerprint
from studio.agents import DrawingAgents
from studio.llm import close_model_clients
from studio.runtime import DrawingRuntime
from studio.settings import ModelSettings
from studio.store import StudioStore


async def evaluate(args):
    if args.database.exists():
        raise ValueError('Use a new independent evaluation database')
    store = StudioStore(args.database)
    settings = ModelSettings(store)
    config = settings.load()
    runtime = DrawingRuntime(store, settings)
    started = time.perf_counter()
    checks, reports = {}, []

    async def show_main(task_id, document):
        pictures, ids = await runtime.vision.context(task_id, 'main', f'main:{task_id}', document)
        model = DrawingAgents(store, config)
        # This is an actual image request. No synthetic model reply or delivered marker is substituted.
        await model.respond(task_id, 'main', 1, [{'role': 'system', 'content':
            'Describe the supplied actual canvas briefly. This is a read-only verification; do not draw or call any tools.'},
            *pictures], observation_ids=ids)
        runtime.vision.delivered(task_id, f'main:{task_id}', ids)
        return model

    async def finish(task, models, call_id):
        return await runtime.call_main_tool(models, task['id'], task['session_id'],
            store.session_document_id(task['session_id']), {'id': call_id,
                'function': {'name': 'finish_drawing', 'arguments': json.dumps({'summary': '完成本轮指定内容。'})}})

    def quality_requests(task_id):
        return sum(item['agent_id'] == 'quality' and item['kind'] == 'llm_response'
                   for item in store.tool_logs(task_id))

    try:
        session = store.create_session('完整人物完成检查')
        task = {**store.create_task(session['id']), 'session_id': session['id']}
        request = args.drawing_request
        store.add_message(session['id'], 'user', request, task['id'])
        runtime.quality.begin(task['id'], session['document'], request)
        native = json.loads(args.rejected_artwork.read_text(encoding='utf-8'))
        document = store.save_snapshot(session['document']['id'], 0, native['elements'], [])
        history = store.get_history(document['id'], 'undo')
        models = await show_main(task['id'], document)
        output = await finish(task, models, 'reject-incomplete-art')
        checks['incomplete_artwork_rejected_at_finish'] = output.get('ok') is False and bool(output.get('assessment'))
        count = quality_requests(task['id'])
        repeated = await finish(task, models, 'same-image-retry')
        checks['unchanged_negative_review_reused_without_request'] = (
            repeated.get('cached') is True and repeated.get('ok') is False and quality_requests(task['id']) == count == 2)
        checks['review_did_not_change_artwork_or_history'] = (
            store.get_document(document['id']) == document and store.get_history(document['id'], 'undo') == history)
        logs = store.tool_logs(task['id'])
        checks['failure_in_full_tool_output'] = output.get('ok') is False and bool(output.get('assessment')) and any(item['kind'] == 'function_call_output' and
            json.loads(item['payload']['output']).get('assessment') == output.get('assessment') for item in logs)
        contexts = [item['payload'] for item in logs if item['kind'] == 'quality_context']
        checks['independent_review_uses_images_without_drawing_tool_history'] = (
            len(contexts) == 1 and bool(contexts[0]['image_observation_ids']) and
            not contexts[0]['drawing_tool_history_included'] and contexts[0]['message_count'] == 2)
        reports.append({'case': 'previous_actual_incomplete_artwork', 'session_id': session['id'],
                        'task_id': task['id'], 'output': output})

        local = store.create_session('局部精确编辑完成检查')
        red = create_native_element({'type': 'ellipse', 'name': '红色圆', 'x': 40, 'y': 60,
            'width': 60, 'height': 60, 'backgroundColor': '#ef4444', 'strokeColor': '#ef4444'})
        blue = create_native_element({'type': 'rectangle', 'name': '蓝色方块', 'x': 220, 'y': 60,
            'width': 60, 'height': 60, 'backgroundColor': '#2563eb', 'strokeColor': '#2563eb'})
        before = store.save_snapshot(local['document']['id'], 0, [red, blue], [red['id']])
        edit = {**store.create_task(local['id']), 'session_id': local['id']}
        request = '把红色圆向右移动20像素，其他图形保持不变。'
        store.add_message(local['id'], 'user', request, edit['id'])
        runtime.quality.begin(edit['id'], before, request)
        after = store.commit_action(before['id'], {'kind': 'move_elements', 'args': {
            'ids': [red['id']], 'dx': 20, 'dy': 0}}, {})['document']
        models = await show_main(edit['id'], after)
        passed = await finish(edit, models, 'approve-precise-edit')
        checks['correct_local_edit_approved_at_finish'] = passed.get('ok') is True and bool(passed.get('quality_review_key'))
        current = store.get_document(after['id'])
        checks['original_ids_and_other_object_preserved'] = (current['elements'][0]['id'] == red['id'] and
            current['elements'][0]['x'] == red['x'] + 20 and
            element_fingerprint(current['elements'][1]) == element_fingerprint(blue))
        cached = await runtime.quality.assess(edit['id'], config)
        checks['unchanged_positive_review_reused_without_request'] = cached.get('ok') is True and cached.get('cached') is True and quality_requests(edit['id']) == 2
        checks['reviews_isolated_between_tasks'] = cached.get('review_key') != output.get('review_key')
        reports.append({'case': 'precise_local_edit', 'session_id': local['id'], 'task_id': edit['id'], 'output': cached})
        result = {'provider': config.provider, 'model': config.model, 'checks': checks, 'results': reports,
            'passed': all(checks.values()), 'latency_ms': round((time.perf_counter() - started) * 1000, 1),
            'tool_origin': 'explicit_runtime_calls_after_actual_main_model_image_delivery',
            'measurement': 'real_model_completion_boundary_review_not_an_end_to_end_artwork_generation',
            'implementation_sha256': {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in (Path('studio/quality.py'), Path('studio/runtime.py'))}}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(result, ensure_ascii=True, indent=2), flush=True)
        return result['passed']
    finally:
        await runtime.close()
        await close_model_clients()
        store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rejected-artwork', type=Path, required=True)
    parser.add_argument('--drawing-request', default='画一幅蒙娜丽莎素描图')
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    load_dotenv(Path(__file__).resolve().parent.parent / '.env', override=False)
    if not asyncio.run(evaluate(args)):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
