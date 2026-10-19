"""Real main/child image-coordinate repair through the production HTTP/browser harness."""

import argparse
import asyncio
import json
import time
from pathlib import Path

import httpx

from studio.actions import element_fingerprint
from studio.agents import prepare_drawing_tool
from studio.eraser import element_geometry
from studio.renderer import CanvasRenderer
from studio.store import StudioStore
from .evaluate_quality import render_final_document
from .evaluation_artifacts import capture_implementation


async def render_face(document):
    renderer = CanvasRenderer()
    try:
        return (await renderer.render(document, {'x': -320, 'y': 140, 'width': 250, 'height': 290}))['png']
    finally:
        await renderer.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--coordinate-space', choices=['image_pixels', 'image_normalized'], default='image_normalized')
    args = parser.parse_args()
    info = json.loads((args.output_dir / 'server.json').read_text(encoding='utf-8'))
    if (info.get('source') != 'owned_isolated_production_http_server' or info['url'] != args.url
            or Path(info['database']).resolve() != args.database.resolve()):
        raise ValueError('Run through the owned HTTP/browser evaluation harness')
    implementation = capture_implementation(args.output_dir)
    with httpx.Client(base_url=args.url, timeout=30, trust_env=False) as client:
        response = client.get('/api/studio/sessions')
        response.raise_for_status()
        if response.json():
            raise ValueError('This controlled repair evaluation requires a fresh empty database')
        health = client.get('/api/studio/health')
        health.raise_for_status()
        if not health.json()['provider_configured']:
            raise ValueError('Configure an actual image/tool model before evaluation')
        store = StudioStore(args.database)
        try:
            if store.list_sessions():
                raise ValueError('Evaluation database is no longer empty')
            session = store.create_session('局部图片坐标改线与擦除')
            fixture_task = store.create_task(session['id'])
            document = session['document']
            for name, arguments in [
                ('create_elements', {'items': [
                    {'type': 'ellipse', 'name': '保持的人脸轮廓', 'x': -290, 'y': 170, 'width': 190, 'height': 230,
                     'strokeWidth': 1.2, 'roughness': 0},
                    {'type': 'ellipse', 'name': '保持的远处蓝圆', 'x': 800, 'y': -180, 'width': 45, 'height': 45,
                     'backgroundColor': '#2563eb', 'strokeColor': '#2563eb', 'roughness': 0},
                ]}),
                ('draw_paths', {'strokeWidth': .5, 'paths': [
                    {'name': '保持的左眼', 'points': [[-260, 255], [-247, 248], [-234, 255]]},
                    {'name': '保持的右眼', 'points': [[-156, 255], [-143, 248], [-130, 255]]},
                    {'name': '需要改成微笑的嘴', 'points': [[-195, 315], [-195, 345]], 'smooth': False},
                    {'name': '需要擦掉的红色斜线', 'points': [[-272, 275], [-245, 308]],
                     'smooth': False, 'strokeColor': '#dc2626', 'strokeWidth': 1},
                ]}),
            ]:
                _, _, prepared, _ = prepare_drawing_tool(name, arguments, document, document['elements'], [])
                for item in prepared:
                    document = store.commit_action(document['id'], item['action'], item['expected_elements'])['document']
            initial = document
            by_name = {item['customData']['name']: item for item in document['elements']}
            mouth = by_name['需要改成微笑的嘴']
            stray = by_name['需要擦掉的红色斜线']
            protected = {item['id']: element_fingerprint(item) for item in document['elements']
                         if item['id'] not in {mouth['id'], stray['id']}}
            store.update_task(fixture_task['id'], 'completed', 'Native repair fixture; no model output was substituted.')
        finally:
            store.close()
        (args.output_dir / 'observed-coordinates.before.png').write_bytes(asyncio.run(render_final_document(initial)))
        prompt = (f"请派一个绘图子 Agent 修正当前简笔人脸，只做以下三处：使用橡皮擦整条擦掉左脸颊的红色斜线（ID {stray['id']}）；"
            f"使用 reshape_path 把原有竖直的嘴（ID {mouth['id']}）改成居中、宽度适中的小弧线微笑；"
            '嘴宽约为双眼中心间距的一半；使用 draw_paths 在双眼之间略下方补一个小巧鼻子，鼻子上端与眼线留出约眼间距八分之一的空隙。'
            '保持人脸轮廓、双眼和远处蓝色圆完全不变，不要标签或其他装饰。'
            '这次专门验证看图修正：三个绘图工具都必须带自己已查看的当前 observation_id，'
            + ('同时带 coordinate_space=image_normalized，points 两轴都使用 0..1000 的归一化图片坐标，'
               '左上为(0,0)、右下为(1000,1000)，擦除 radius 按图片长边的千分比表示，不要自行换算像素或画布坐标。'
               if args.coordinate_space == 'image_normalized' else
               'points 直接使用对应 PNG 的 width/height 像素坐标，擦除 radius 也用像素，不要自行猜算画布坐标。') +
            '可用 view_canvas 查看人脸局部区域 x=-320,y=140,width=250,height=290；改变草稿后使用新图片的 view_ ID。'
            '只修正这三处，子 Agent 完成后由主 Agent 查看拟提交图片并提交，检查实际画布后结束。')
        response = client.post(f"/api/studio/sessions/{session['id']}/messages", json={'message': prompt})
        response.raise_for_status()
        task_id = response.json()['id']
        case = {'case': 'observed-coordinates', 'session_id': session['id'], 'task_id': task_id}
        print(json.dumps(case), flush=True)
        try:
            while True:
                response = client.get(f'/api/studio/tasks/{task_id}')
                response.raise_for_status()
                task = response.json()
                if task['status'] in {'completed', 'partial', 'failed', 'cancelled'}:
                    break
                time.sleep(.2)
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
        image_tools = {item['payload']['name'] for item in calls
            if item['payload']['name'] in {'draw_paths', 'reshape_path', 'erase_strokes'}
            and json.loads(item['payload']['arguments']).get('observation_id')
            and (json.loads(item['payload']['arguments']).get('coordinate_space') or 'image_pixels') == args.coordinate_space}
        present = {item['id']: item for item in final['elements'] if not item.get('isDeleted')}
        mouth_geometry = element_geometry(present[mouth['id']]) if mouth['id'] in present else None
        added = [item for item in present.values() if item['id'] not in {old['id'] for old in initial['elements']}]
        errors = {(item['agent_id'], item['payload']['call_id']) for item in logs if item['kind'] == 'function_call_output'
                  and json.loads(item['payload']['output']).get('ok') is False}
        delivered = {(item['agent_id'], call_id) for item in logs if item['kind'] == 'model_context'
                     for call_id in item['payload'].get('tool_error_ids', [])}
        mouth_bounds = list(mouth_geometry.bounds) if mouth_geometry is not None else None
        checks = {
            'actual_model_task_completed': task['status'] == 'completed',
            'main_dispatches_child': any(item['agent_id'] == 'main' and item['payload']['name'] == 'spawn_agent' for item in calls),
            'all_three_tools_use_requested_image_coordinates': image_tools == {'draw_paths', 'reshape_path', 'erase_strokes'},
            'main_reviews_proposed_pixels': any(item['agent_id'] == 'main' and item['kind'] == 'model_context'
                and item['payload'].get('result_observation_ids') for item in logs),
            'main_submits_child_result': any(item['agent_id'] == 'main' and item['payload']['name'] == 'apply_agent_result' for item in calls),
            'red_stroke_erased': stray['id'] not in present,
            'protected_objects_unchanged': all(identity in present and element_fingerprint(present[identity]) == fingerprint
                                             for identity, fingerprint in protected.items()),
            'mouth_remains_same_editable_object': mouth_geometry is not None and present[mouth['id']]['type'] == 'freedraw',
            'mouth_centered_below_eyes_and_wider_than_tall': bool(mouth_bounds) and
                -225 <= (mouth_bounds[0] + mouth_bounds[2]) / 2 <= -165 and
                300 <= mouth_bounds[1] <= 350 and 30 <= mouth_bounds[2] - mouth_bounds[0] <= 100 and
                0 < mouth_bounds[3] - mouth_bounds[1] < 30,
            'small_native_nose_between_eyes_and_mouth': bool(added) and all(item['type'] == 'freedraw' and
                -225 <= item['x'] and item['x'] + item['width'] <= -165 and
                265 <= item['y'] and item['y'] + item['height'] <= 310 for item in added),
            'tool_errors_delivered_in_full': errors <= delivered,
        }
        for direction in ('undo', 'redo'):
            response = client.post(f"/api/studio/documents/{final['id']}/history", json={'direction': direction})
            response.raise_for_status()
        checks['redo_restores_exact_final_native_content'] = response.json()['elements'] == final['elements']
        (args.output_dir / 'observed-coordinates.png').write_bytes(asyncio.run(render_final_document(final)))
        (args.output_dir / 'observed-coordinates.face.png').write_bytes(asyncio.run(render_face(final)))
        (args.output_dir / 'observed-coordinates.excalidraw').write_text(json.dumps({'type': 'excalidraw', 'version': 2,
            'source': 'draw-agent-studio', 'elements': final['elements'], 'appState': {'viewBackgroundColor': '#ffffff'}, 'files': {}},
            ensure_ascii=False), encoding='utf-8')
        result = {**case, 'status': task['status'], 'latency_ms': task['latency_ms'], 'error': task['error'],
            'requested_coordinate_space': args.coordinate_space,
            'scope': 'real main/child image-coordinate local repair; controlled native fixture, not full-art acceptance',
            'source_fixture': 'native cartoon face at negative coordinates and a distant blue circle; no model replies mocked',
            'checks': checks, 'tool_error_count': len(errors), 'passed': all(checks.values()), 'mouth_bounds': mouth_bounds,
            'implementation_sha256': implementation, 'model_context_count': sum(item['kind'] == 'model_context' for item in logs)}
        (args.output_dir / 'report.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({key: result[key] for key in ('case', 'session_id', 'task_id', 'status', 'latency_ms', 'model_context_count', 'passed')}), flush=True)
        if not result['passed']:
            raise SystemExit(1)


if __name__ == '__main__':
    main()
