"""Read-only paired facial-coordinate probes from a real recorded drawing step.

Checks frame coherence with an existing head contour, not likeness or whole-art
quality. Tools returned by the real model are validated but never executed.
"""

import argparse
import asyncio
import base64
import json
import sqlite3
import time
from pathlib import Path

from dotenv import load_dotenv
from shapely import Point, Polygon

from studio.agent_tools import CHILD_TOOLS, validate_tool
from studio.image_guide import coordinate_guide
from studio.llm import ModelGateway, close_model_clients
from studio.prompts import CHILD_SYSTEM
from studio.settings import ModelSettings
from studio.store import StudioStore
from .evaluate_read_compaction import save_input
from .evaluation_artifacts import capture_implementation


def frame_checks(message, arguments, parent):
    calls = message.get('tool_calls') or []
    if len(calls) != 1 or calls[0]['function']['name'] != 'draw_paths':
        return {'valid_source_call': False, 'error': 'Expected one draw_paths call'}
    try:
        args = validate_tool('draw_paths', json.loads(calls[0]['function']['arguments']), child=True)
    except (ValueError, KeyError, TypeError) as error:
        return {'valid_source_call': False, 'error': str(error)}
    source_call = all(args.get(key) == arguments[key]
                      for key in ('reference_view_id', 'reference_layout_id', 'coordinate_space'))
    names = {item['name']: item['points'] for item in args['paths']}
    expected = {'left_eye', 'right_eye', 'nose', 'mouth'}
    if set(names) != expected:
        return {'valid_source_call': source_call, 'complete_feature_set': False,
                'returned_names': list(names)}
    shape = Polygon(parent)
    if not shape.is_valid or shape.area <= 0:
        raise ValueError('The selected recorded parent contour must form a valid closed region')
    centers = {name: [sum(p[axis] for p in points) / len(points) for axis in (0, 1)]
               for name, points in names.items()}
    lo, hi = min(y for _, y in parent), max(y for _, y in parent)
    fractions = {name: (center[1] - lo) / (hi - lo) for name, center in centers.items()}
    return {'valid_source_call': source_call, 'complete_feature_set': True,
        'centers_inside_recorded_head': all(shape.covers(Point(point)) for point in centers.values()),
        'eyes_in_upper_middle_of_recorded_head': all(.25 <= fractions[name] <= .6 for name in ('left_eye', 'right_eye')),
        'mouth_in_lower_part_of_recorded_head': .65 <= fractions['mouth'] <= .95,
        'left_right_order': centers['left_eye'][0] < centers['right_eye'][0],
        'vertical_order': max(centers[name][1] for name in ('left_eye', 'right_eye')) < centers['nose'][1] < centers['mouth'][1],
        'centers': centers, 'head_y_fractions': fractions}


async def evaluate(args):
    if args.database.exists() or args.output_dir.exists():
        raise ValueError('Use new isolated output paths')
    with sqlite3.connect(args.source_database.resolve().as_uri() + '?mode=ro', uri=True) as source:
        args.database.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(args.database) as target:
            source.backup(target)
    args.output_dir.mkdir(parents=True)
    implementation = capture_implementation(args.output_dir)
    store = StudioStore(args.database)
    try:
        row = store.connection.execute("SELECT * FROM agent_tool_calls WHERE caller_id=? AND name='draw_paths' "
            "AND json_extract(output,'$.ok')=1 ORDER BY created_at LIMIT 1", (args.run_id,)).fetchone()
        if row is None:
            raise ValueError('The source run has no successful drawing call')
        arguments = json.loads(row['arguments'])
        if arguments.get('coordinate_space') != 'image_normalized' or not arguments.get('reference_layout_id'):
            raise ValueError('Choose a source-normalized drawing step')
        parent = next(path['points'] for path in arguments['paths'] if path['name'] == args.parent_path)
        if len(parent) < 4 or parent[0] != parent[-1]:
            raise ValueError('Choose the exact name of a closed recorded head contour')
        ref = store.connection.execute('SELECT * FROM reference_image_views WHERE id=? AND caller_id=?',
            (arguments['reference_view_id'], args.run_id)).fetchone()
        view = store.connection.execute("SELECT * FROM canvas_observations WHERE caller_id=? AND region='null' "
            'AND created_at>? ORDER BY created_at LIMIT 1', (args.run_id, row['created_at'])).fetchone()
        if ref is None or view is None:
            raise ValueError('The recorded source view and first post-draw image must exist')
        ref_meta = json.loads(ref['metadata'])
        canvas_meta = json.loads(view['metadata'])
        config = ModelSettings(store).load()
        gateway = ModelGateway(config)
        tool = next(item for item in CHILD_TOOLS if item['function']['name'] == 'draw_paths')
        report = {'scope': __doc__, 'source_database': str(args.source_database), 'source_opened_read_only': True,
            'database': str(args.database), 'run_id': args.run_id, 'recorded_call_id': row['call_id'],
            'recorded_parent_path': args.parent_path, 'parent_source_normalized_points': parent,
            'reference_view_id': ref['id'], 'canvas_observation_id': view['id'], 'canvas_scene_hash': view['scene_hash'],
            'provider': config.provider, 'model': config.model, 'tools_executed': False,
            'artwork_acceptance': False, 'implementation': implementation, 'cases': []}
        for repeat in range(args.repeats):
            for guided in (False, True):
                name = f'{repeat}-' + ('rulers' if guided else 'original')
                metadata = {**ref_meta}
                png = coordinate_guide(ref['png'], units='image_normalized') if guided else ref['png']
                if guided:
                    metadata['coordinate_guide'] = {'units': 'image_normalized', 'grid_spacing': 100,
                        'instruction': 'Blue labels are 0..1000 source-view coordinates, never original pixels or drawn artwork.'}
                content = [{'type': 'text', 'text': json.dumps({'diagnostic_request':
                    'Place only the four fine facial feature contours left_eye, right_eye, nose and mouth according to '
                    'the SOURCE portrait. Return exactly one draw_paths call with exactly these four path names. '
                    'Use a few structural anchors for each contour, not a complete new head or body. '
                    'Use the supplied reference_view_id, reference_layout_id and coordinate_space=image_normalized; '
                    'both source-view axes span 0..1000. The existing draft below is a separate image for orientation. '
                    'This is a read-only coordinate diagnostic: returned tools will not be executed.',
                    'reference_view_id': arguments['reference_view_id'],
                    'reference_layout_id': arguments['reference_layout_id'], 'coordinate_space': 'image_normalized'}, ensure_ascii=False)},
                    {'type': 'text', 'text': json.dumps({'reference_image': metadata})},
                    {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + base64.b64encode(png).decode(), 'detail': 'high'}},
                    {'type': 'text', 'text': json.dumps({'recorded_private_draft': canvas_meta})},
                    {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + base64.b64encode(view['png']).decode(), 'detail': 'high'}}]
                messages = [{'role': 'system', 'content': CHILD_SYSTEM}, {'role': 'user', 'content': content}]
                save_input(messages, args.output_dir / name)
                started = time.perf_counter()
                response = await gateway.complete(messages, [tool])
                message = response.choices[0].message.model_dump(exclude_none=True)
                result = {'name': name, 'rulers': guided, 'latency_ms': round((time.perf_counter() - started) * 1000, 1),
                    'message': message, 'usage': response.usage.model_dump() if response.usage else None,
                    'checks': frame_checks(message, arguments, parent)}
                report['cases'].append(result)
                (args.output_dir / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
                print(json.dumps({key: value for key, value in result.items() if key not in {'message', 'usage'}}, ensure_ascii=False), flush=True)
    finally:
        await close_model_clients()
        store.close()


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-database', type=Path, required=True)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--parent-path', required=True)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--repeats', type=int, default=2)
    args = parser.parse_args()
    if args.repeats <= 0:
        parser.error('--repeats must be positive')
    asyncio.run(evaluate(args))


if __name__ == '__main__':
    main()
