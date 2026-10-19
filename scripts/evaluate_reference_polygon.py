"""Real main/child reference tracing or source-anchor drawing through production HTTP."""

import argparse
import asyncio
import io
import json
import time
from pathlib import Path

import httpx
from PIL import Image, ImageDraw
from shapely import LineString, union_all

from studio.actions import create_native_element, element_fingerprint
from studio.reference_images import ReferenceImages, decode_reference
from studio.store import StudioStore
from .evaluate_quality import render_final_document
from .evaluation_artifacts import capture_implementation


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--method', choices=['polygon', 'layout'], default='polygon')
    args = parser.parse_args()
    info = json.loads((args.output_dir / 'server.json').read_text(encoding='utf-8'))
    if (info.get('source') != 'owned_isolated_production_http_server' or info['url'] != args.url
            or Path(info['database']).resolve() != args.database.resolve()):
        raise ValueError('Run this check through the owned HTTP/browser evaluation harness')
    implementation = capture_implementation(args.output_dir)
    with httpx.Client(base_url=args.url, timeout=30, trust_env=False) as client:
        response = client.get('/api/studio/sessions')
        response.raise_for_status()
        if response.json():
            raise ValueError('The reference-region fixture requires a fresh, empty evaluation server')
        health = client.get('/api/studio/health')
        health.raise_for_status()
        if not health.json()['provider_configured']:
            raise ValueError('Configure a real image/tool model before evaluation')
        # Seed only this confirmed-empty owned database, before any model work.
        # Do not instantiate DrawingRuntime/AgentManager: their recovery is a startup operation.
        store = StudioStore(args.database)
        try:
            if store.list_sessions():
                raise ValueError('The evaluation database is no longer empty')
            session = store.create_session('多边形主体与独立对象')
            fixture_task = store.create_task(session['id'])
            image = Image.new('RGB', (256, 256), 'white')
            drawing = ImageDraw.Draw(image)
            drawing.line([(150, 32), (190, 110), (110, 110), (150, 32)], fill='black', width=3)
            drawing.line([(95, 25), (125, 25)], fill='black', width=3)
            buffer = io.BytesIO()
            image.save(buffer, format='PNG')
            source = ReferenceImages(store).save_image(fixture_task['id'], 'evaluation-fixture', {
                'title': '三角轮廓与不属于主体的短线', 'url': 'https://example.org/evaluation/triangle',
                'image': 'https://example.org/evaluation/triangle.png'}, buffer.getvalue(), decode_reference(buffer.getvalue()))
            (args.output_dir / 'source.png').write_bytes(buffer.getvalue())
            unrelated = create_native_element({'type': 'ellipse', 'name': '保留的蓝色圆',
                'x': 500, 'y': 40, 'width': 30, 'height': 30, 'backgroundColor': '#2563eb'})
            before = store.save_snapshot(session['document']['id'], 0, [unrelated], [])
            store.update_task(fixture_task['id'], 'completed', 'Evaluation source attached; no model reply was substituted.')
        finally:
            store.close()
        prompt = (f"请创建一个绘图子 Agent，查看本画布保存的参考图 {source['reference_id']}，"
            '只提取中间三角轮廓，排除三角形左上方的独立短横线。参考图为256×256像素，'
            '使用原生 trace_reference 的 source_polygon 选区、ink模式，并用 limit=1 分页；有 next_page 时按原计划继续。'
            '共同 source_frame 使用整张参考图，target 为 x=0,y=0,width=512,height=512。'
            '不要重新手工猜画，不要使用文字标签，也不要修改已有蓝色圆。主 Agent 审阅并提交结果，检查当前图片后完成。')
        if args.method == 'layout':
            prompt = (f"请先查看本画布保存的完整参考图 {source['reference_id']}，"
                '在下一轮使用 create_reference_layout 定义统一布局：source_frame 为 x=80,y=10,width=130,height=150，'
                'target 为 x=-400,y=200,width=600,height=600，保持比例并居中。然后派一个绘图子 Agent，给它该布局编号。'
                '子 Agent 先在自己上下文查看完整来源，再用 view_reference_image 查看 x=100,y=20,width=100,height=110 的局部。'
                '只画三角轮廓，排除左上方的独立短横线。使用 draw_paths，smooth=false、closed=true，'
                '带自己已查看的局部 reference_view_id、主 Agent 的 reference_layout_id 和 coordinate_space=image_normalized。'
                '关键点直接落在局部参考 PNG 上，两个轴均为0..1000；由 runtime 换算画布位置，不要手工猜算画布坐标，'
                '不要调用 trace_reference，不要文字标签，也不要改动原有蓝色圆。主 Agent 查看拟提交图片并提交，检查实际画布后完成。')
        response = client.post(f"/api/studio/sessions/{session['id']}/messages", json={'message': prompt})
        response.raise_for_status()
        task_id = response.json()['id']
        case = {'case': 'reference-layout' if args.method == 'layout' else 'reference-polygon',
                'session_id': session['id'], 'task_id': task_id}
        print(json.dumps(case), flush=True)
        try:
            while True:
                response = client.get(f'/api/studio/tasks/{task_id}')
                response.raise_for_status()
                task = response.json()
                if task['status'] in {'completed', 'partial', 'failed', 'cancelled'}:
                    break
                time.sleep(0.2)
        except KeyboardInterrupt:
            client.post(f'/api/studio/tasks/{task_id}/cancel').raise_for_status()
            raise
        response = client.get(f"/api/studio/sessions/{session['id']}")
        response.raise_for_status()
        final = response.json()['document']
        response = client.get(f'/api/studio/tasks/{task_id}/logs')
        response.raise_for_status()
        logs = response.json()
        calls = [item for item in logs if item['kind'] == 'function_call']
        new = [item for item in final['elements'] if item['id'] != unrelated['id'] and not item.get('isDeleted')]
        curves = [LineString([(item['x'] + x, item['y'] + y) for x, y in item['points']])
                  for item in new if item['type'] in {'freedraw', 'line'}]
        expected = (LineString([(-80, 288), (80, 600), (-240, 600), (-80, 288)]) if args.method == 'layout'
                    else LineString([(300, 64), (380, 220), (220, 220), (300, 64)]))
        corner = (LineString([(-300, 260), (-180, 260)]) if args.method == 'layout'
                  else LineString([(190, 50), (250, 50)]))
        geometry = union_all(curves)
        geometry_error = geometry.hausdorff_distance(expected) if curves else None
        errors = {(item['agent_id'], item['payload']['call_id']) for item in logs if item['kind'] == 'function_call_output'
                  and json.loads(item['payload']['output']).get('ok') is False}
        delivered = {(item['agent_id'], call_id) for item in logs if item['kind'] == 'model_context'
                     for call_id in item['payload'].get('tool_error_ids', [])}
        checks = {
            'actual_model_task_completed': task['status'] == 'completed',
            'main_dispatches_child': any(item['agent_id'] == 'main' and item['payload']['name'] == 'spawn_agent' for item in calls),
            'main_reviews_proposed_pixels': any(item['agent_id'] == 'main' and item['kind'] == 'model_context'
                and item['payload'].get('result_observation_ids') for item in logs),
            'main_commits_result': any(item['agent_id'] == 'main' and item['payload']['name'] == 'apply_agent_result' for item in calls),
            # Both modes allow 5 source pixels: 10 canvas units at scale 2,
            # or 20 at scale 4. A cropped view must never be repositioned.
            'native_geometry_follows_source_target_mapping': geometry_error is not None and geometry_error <= (20 if args.method == 'layout' else 10),
            'corner_background_excluded': bool(curves) and not geometry.intersects(corner),
            'unrelated_object_preserved': any(item['id'] == unrelated['id'] and element_fingerprint(item) == element_fingerprint(unrelated)
                for item in final['elements']),
            'all_new_content_is_editable_native_paths': bool(new) and len(curves) == len(new),
            'tool_errors_delivered_in_full': errors <= delivered,
        }
        if args.method == 'polygon':
            checks['child_uses_source_polygon'] = any(item['agent_id'] != 'main' and item['payload']['name'] == 'trace_reference'
                and json.loads(item['payload']['arguments']).get('source_polygon') for item in calls)
        else:
            source_outputs = [json.loads(item['payload']['output']) for item in logs
                if item['agent_id'] != 'main' and item['kind'] == 'function_call_output'
                and json.loads(item['payload']['output']).get('coordinate_frame', {}).get('input_space') == 'reference_image_normalized']
            checks.update(
                main_defines_layout=any(item['agent_id'] == 'main' and item['payload']['name'] == 'create_reference_layout' for item in calls),
                child_draws_from_own_cropped_reference=any(output.get('ok') and
                    output['coordinate_frame']['source_view_bounds'] == {'x': 100, 'y': 20, 'width': 100, 'height': 110}
                    for output in source_outputs),
                source_anchor_drawing_not_automatic_extraction=bool(source_outputs) and not any(
                    item['payload']['name'] in {'trace_reference', 'continue_reference_trace'} for item in calls))
        response = client.post(f"/api/studio/documents/{final['id']}/history", json={'direction': 'undo'})
        response.raise_for_status()
        response = client.post(f"/api/studio/documents/{final['id']}/history", json={'direction': 'redo'})
        response.raise_for_status()
        checks['redo_restores_exact_final_native_content'] = response.json()['elements'] == final['elements']
        (args.output_dir / f"{case['case']}.png").write_bytes(asyncio.run(render_final_document(final)))
        (args.output_dir / f"{case['case']}.excalidraw").write_text(json.dumps({'type': 'excalidraw', 'version': 2,
            'source': 'draw-agent-studio', 'elements': final['elements'], 'appState': {'viewBackgroundColor': '#ffffff'}, 'files': {}},
            ensure_ascii=False), encoding='utf-8')
        result = {**case, 'status': task['status'], 'latency_ms': task['latency_ms'], 'error': task['error'],
            'scope': f'real main/child reference {args.method} and native projection; controlled source fixture, not full-art acceptance',
            'source_fixture': 'generated triangle and independent corner line; no model replies were mocked',
            'checks': checks, 'tool_error_count': len(errors), 'passed': all(checks.values()),
            'source_mapping_hausdorff_error_units': geometry_error,
            'implementation_sha256': implementation, 'model_context_count': sum(item['kind'] == 'model_context' for item in logs)}
        (args.output_dir / 'report.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({key: result[key] for key in ('case', 'session_id', 'task_id', 'status', 'latency_ms', 'model_context_count', 'passed')}), flush=True)
        if not result['passed']:
            raise SystemExit(1)


if __name__ == '__main__':
    main()
