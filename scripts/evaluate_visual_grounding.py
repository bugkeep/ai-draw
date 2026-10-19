"""Read-only real-model localization diagnostics; not artwork acceptance."""

import argparse
import asyncio
import base64
import hashlib
import io
import json
import math
import time
from collections import defaultdict
from pathlib import Path
from statistics import mean

from dotenv import load_dotenv
from PIL import Image

from studio.actions import create_native_element
from studio.agents import prepare_drawing_tool
from studio.image_guide import coordinate_guide
from studio.llm import ModelGateway, close_model_clients
from studio.settings import ModelSettings
from studio.store import StudioStore
from studio.vision import CanvasVision
from .evaluation_artifacts import capture_implementation


async def evaluate(args):
    if args.database.exists():
        raise ValueError('Use a new isolated diagnostics database')
    args.output_dir.mkdir(parents=True, exist_ok=True)
    implementation = capture_implementation(args.output_dir)
    store = StudioStore(args.database)
    vision = CanvasVision(store)
    measurements = []
    try:
        config = ModelSettings(store).load()
        gateway = ModelGateway(config)
        session = store.create_session('只读图片定位对照')
        task = store.create_task(session['id'])
        document = session['document']
        elements = [create_native_element({'type': 'ellipse', 'x': -290, 'y': 170, 'width': 190, 'height': 230}),
                    create_native_element({'type': 'ellipse', 'x': 800, 'y': -180, 'width': 45, 'height': 45,
                                           'backgroundColor': '#2563eb'})]
        drawing, _, _, _ = prepare_drawing_tool('draw_paths', {'strokeWidth': .5, 'paths': [
            {'name': 'left_eye', 'points': [[-260, 255], [-247, 248], [-234, 255]]},
            {'name': 'right_eye', 'points': [[-156, 255], [-143, 248], [-130, 255]]},
            {'name': 'mouth', 'points': [[-195, 315], [-195, 345]], 'smooth': False},
            {'name': 'red_mark', 'points': [[-272, 275], [-245, 308]], 'smooth': False,
             'strokeColor': '#dc2626', 'strokeWidth': 1},
        ]}, document, [], [])
        document = store.save_snapshot(document['id'], 0, elements + drawing, [])
        before = json.dumps(document, sort_keys=True)
        history = store.get_history(document['id'], 'undo')
        full = await vision.observe(task['id'], 'diagnostic', 'diagnostic', document)
        focus = await vision.observe(task['id'], 'diagnostic', 'diagnostic', document,
            {'x': -320, 'y': 140, 'width': 250, 'height': 290})
        raw_focus = vision.image(session['id'], focus['observation_id'])
        raw_full = vision.image(session['id'], full['observation_id'])
        (args.output_dir / 'source.png').write_bytes(raw_focus)
        truth = {item['customData']['name']: [item['x'] + item['width'] / 2, item['y'] + item['height'] / 2]
                 for item in drawing}
        store.connection.execute('CREATE TABLE visual_grounding_cases '
            '(id TEXT PRIMARY KEY, png BLOB NOT NULL, metadata TEXT NOT NULL, response TEXT, metrics TEXT)')
        store.connection.commit()
        variants = [(1280, False, False, 'pixels'), (1280, False, False, 'normalized'),
                    (1280, True, False, 'pixels'), (1280, True, False, 'normalized'),
                    (1024, False, False, 'pixels'), (640, False, False, 'pixels'),
                    (1280, False, True, 'pixels'), (1280, False, True, 'normalized')]
        semaphore = asyncio.Semaphore(2)

        async def one(max_size, grid, dual, units, repeat):
            identifier = f'{max_size}-grid{int(grid)}-dual{int(dual)}-{units}-{repeat}'
            with Image.open(io.BytesIO(raw_focus)) as original:
                picture = original.copy()
            picture.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
            width, height = picture.size
            buffer = io.BytesIO()
            picture.save(buffer, format='PNG')
            png = coordinate_guide(buffer.getvalue(), units='image_pixels') if grid else buffer.getvalue()
            metadata = {'id': identifier, 'width': width, 'height': height, 'grid': grid,
                'dual_images': dual, 'units': units, 'sha256': hashlib.sha256(png).hexdigest()}
            store.connection.execute('INSERT INTO visual_grounding_cases VALUES(?,?,?,NULL,NULL)',
                (identifier, png, json.dumps(metadata)))
            store.connection.commit()
            unit_text = (f'original PNG pixels: x=0..{width}, y=0..{height}' if units == 'pixels'
                         else 'normalized image coordinates: both x and y run from 0 to 1000 over the entire PNG')
            content = []
            if dual:
                content += [{'type': 'text', 'text': 'CURRENT FULL CANVAS: overview only; do not report coordinates in this first image.'},
                            {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + base64.b64encode(raw_full).decode(), 'detail': 'high'}}]
            content += [{'type': 'text', 'text': f'CURRENT FOCUSED PNG ({width} x {height}). Locate the center of the red diagonal mark, '
                'the left eye, the right eye and the mouth. Report centers of their visible bounding boxes. '
                f'Use {unit_text}. Top-left is (0,0); x goes right and y down. '
                'Blue rulers, if present, label original PNG pixels and are not drawn objects. '
                'Only use this focused image for the answer, even when an overview is present.'},
                {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + base64.b64encode(png).decode(), 'detail': 'high'}}]
            tools = [{'type': 'function', 'function': {'name': 'report_locations', 'description': 'Report the four visible feature centers.',
                'parameters': {'type': 'object', 'properties': {key: {'type': 'array', 'items': {'type': 'number'},
                    'minItems': 2, 'maxItems': 2} for key in truth}, 'required': list(truth), 'additionalProperties': False}}}]
            async with semaphore:
                started = time.perf_counter()
                response = await gateway.complete([{'role': 'system', 'content': 'Inspect the supplied actual PNG and call report_locations once. Do not draw or edit.'},
                    {'role': 'user', 'content': content}], tools)
                latency = (time.perf_counter() - started) * 1000
            message = response.choices[0].message.model_dump(exclude_none=True)
            calls = message.get('tool_calls', [])
            metrics = {**metadata, 'latency_ms': round(latency, 1), 'valid': False,
                       'usage': response.usage.model_dump() if response.usage else {}}
            if len(calls) == 1 and calls[0]['function']['name'] == 'report_locations':
                try:
                    locations = json.loads(calls[0]['function']['arguments'])
                    if set(locations) != set(truth):
                        raise ValueError('Missing/extra locations')
                    errors = {}
                    for key, expected in truth.items():
                        x, y = locations[key]
                        if not all(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) for value in (x, y)):
                            raise ValueError('Invalid point')
                        denominator_x, denominator_y = (width, height) if units == 'pixels' else (1000, 1000)
                        actual = [-320 + x / denominator_x * 250, 140 + y / denominator_y * 290]
                        errors[key] = {'canvas_point': actual, 'error_units': round(math.dist(actual, expected), 3)}
                    metrics.update(valid=True, errors=errors)
                except (KeyError, TypeError, ValueError) as error:
                    metrics['error'] = str(error)
            store.connection.execute('UPDATE visual_grounding_cases SET response=?,metrics=? WHERE id=?',
                (json.dumps(message, ensure_ascii=False), json.dumps(metrics), identifier))
            store.connection.commit()
            measurements.append(metrics)
            print(json.dumps({key: metrics[key] for key in ('id', 'latency_ms', 'valid')}), flush=True)

        await asyncio.gather(*(one(*variant, repeat) for variant in variants for repeat in range(args.repeats)))
        unchanged = before == json.dumps(store.get_document(document['id']), sort_keys=True)
        unchanged = unchanged and history == store.get_history(document['id'], 'undo')
        groups = defaultdict(list)
        for measurement in measurements:
            groups[measurement['id'].rsplit('-', 1)[0]].extend(
                value['error_units'] for value in measurement.get('errors', {}).values())
        store.update_task(task['id'], 'completed', 'Read-only model localization measurement; no drawing success claimed.')
        report = {'scope': 'read-only real-model spatial localization, not artwork acceptance', 'model': config.model,
                  'provider': config.provider, 'native_scene_and_history_unchanged': unchanged,
                  'implementation_sha256': implementation,
                  'groups': {key: {'n': len(errors), 'mean_error_units': round(mean(errors), 3),
                                  'max_error_units': max(errors), 'within_8_units': sum(value <= 8 for value in errors)}
                             for key, errors in sorted(groups.items()) if errors},
                  'ground_truth_canvas': truth, 'measurements': sorted(measurements, key=lambda item: item['id'])}
        (args.output_dir / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    finally:
        await close_model_clients()
        await vision.close()
        store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--repeats', type=int, default=2)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error('--repeats must be positive')
    load_dotenv()
    asyncio.run(evaluate(args))


if __name__ == '__main__':
    main()
