"""Immutable composition mappings shared by source-guided drawing children."""

import json
import math
import time

from .agent_store import stable_id
from .agent_tools import CreateReferenceLayout
from .coordinates import map_observed_arguments


def reference_mapping(frame, target):
    scale = min(target['width'] / frame['width'], target['height'] / frame['height'])
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError('Reference mapping must have a finite positive scale')
    return {'origin': [target['x'] + (target['width'] - frame['width'] * scale) / 2 - frame['x'] * scale,
                       target['y'] + (target['height'] - frame['height'] * scale) / 2 - frame['y'] * scale],
            'scale': scale}


class ReferenceLayouts:
    def __init__(self, images):
        self.images, self.store = images, images.store
        self.store.connection.executescript('''
            CREATE TABLE IF NOT EXISTS reference_layouts (
                id TEXT PRIMARY KEY,
                reference_id TEXT NOT NULL REFERENCES reference_images(id) ON DELETE CASCADE,
                task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                arguments TEXT NOT NULL, created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_reference_layouts_source ON reference_layouts(reference_id,created_at);
        ''')
        self.store.connection.commit()

    def _require_source_view(self, task_id, caller_id, reference_id):
        with self.store.lock:
            viewed = self.store.connection.execute(
                'SELECT 1 FROM reference_image_views WHERE task_id=? AND caller_id=? AND reference_id=? '
                "AND region='null' AND delivered_at IS NOT NULL", (task_id, caller_id, reference_id),
            ).fetchone()
        if not viewed:
            raise ValueError('View the full reference in your own model context before defining or using its drawing layout')

    def create(self, task_id, caller_id, arguments):
        if caller_id != f'main:{task_id}':
            raise ValueError('Only the main Agent defines shared reference layouts')
        args = CreateReferenceLayout.model_validate(arguments).model_dump()
        source = json.loads(self.images._record(task_id, args['reference_id'])['metadata'])
        self._require_source_view(task_id, caller_id, args['reference_id'])
        from .reference_images import pixel_region
        frame = pixel_region(args['source_frame'], source['width'], source['height'])
        mapping = reference_mapping(frame, args['target'])
        corners = [mapping['origin'][axis] + value * mapping['scale'] for axis, values in enumerate(
            [(frame['x'], frame['x'] + frame['width']), (frame['y'], frame['y'] + frame['height'])]) for value in values]
        if not all(math.isfinite(value) and abs(value) <= 100000 for value in corners):
            raise ValueError('Mapped reference layout exceeds the canvas coordinate range')
        plan = {**args, 'source_frame': frame, 'mapping': mapping, 'mapping_version': 1}
        encoded = json.dumps(plan, sort_keys=True)
        layout_id = stable_id('layout', f'{task_id}/{encoded}')
        with self.store.lock, self.store.connection:
            self.store.connection.execute('INSERT OR IGNORE INTO reference_layouts VALUES(?,?,?,?,?)',
                (layout_id, args['reference_id'], task_id, encoded, time.time()))
        return {'ok': True, 'reference_layout_id': layout_id, **plan,
                'instruction': 'Give this same layout ID to all children. Each child views the full reference in its '
                    'own context, then uses its own reference_view_id with this layout and coordinate_space=image_normalized. '
                    'Anchors use that viewed source PNG, including close-ups; runtime maps them to canvas. '
                    'The layout is fixed; use current canvas observations after moving existing artwork.'}

    def _record(self, task_id, layout_id):
        with self.store.lock:
            row = self.store.connection.execute(
                'SELECT l.* FROM reference_layouts l JOIN tasks original ON original.id=l.task_id '
                'JOIN tasks current ON current.id=? WHERE l.id=? AND original.session_id=current.session_id '
                'AND original.rowid<=current.rowid', (task_id, layout_id),
            ).fetchone()
        if row is None:
            raise KeyError('Reference layout belongs to another canvas or a later task, or does not exist')
        plan = json.loads(row['arguments'])
        if plan.get('mapping_version') != 1:
            raise ValueError('Unsupported reference layout mapping version')
        return plan

    def recent(self, task_id, reference_id):
        with self.store.lock:
            rows = self.store.connection.execute(
                'SELECT l.id,l.arguments FROM reference_layouts l JOIN tasks original ON original.id=l.task_id '
                'JOIN tasks current ON current.id=? WHERE l.reference_id=? '
                'AND original.session_id=current.session_id AND original.rowid<=current.rowid '
                'ORDER BY l.created_at DESC LIMIT 3', (task_id, reference_id),
            ).fetchall()
        return [{'reference_layout_id': row['id'], **{key: value for key, value in json.loads(row['arguments']).items()
                 if key in {'reference_id', 'name', 'source_frame', 'target', 'mapping'}}} for row in rows]

    def ground_tool(self, task_id, caller_id, document, name, arguments):
        if not (arguments.get('reference_view_id') or arguments.get('reference_layout_id')):
            return arguments, {}
        if name not in {'draw_paths', 'reshape_path'}:
            raise ValueError('Reference layouts ground drawing/reshaping; use the current canvas image for erasing')
        if arguments.get('observation_id') or arguments.get('coordinate_space') != 'image_normalized':
            raise ValueError('Reference anchors require image_normalized coordinates and no canvas observation_id')
        session_id = self.store.get_task(task_id)['session_id']
        if self.store.session_document_id(session_id) != document['id']:
            raise ValueError('Reference drawing targets another canvas')
        plan = self._record(task_id, arguments.get('reference_layout_id'))
        self.images._record(task_id, plan['reference_id'])
        self._require_source_view(task_id, caller_id, plan['reference_id'])
        with self.store.lock:
            view = self.store.connection.execute(
                'SELECT metadata,delivered_at FROM reference_image_views WHERE id=? AND task_id=? AND caller_id=? '
                'AND session_id=? AND reference_id=?',
                (arguments.get('reference_view_id'), task_id, caller_id, session_id, plan['reference_id']),
            ).fetchone()
        if view is None:
            raise ValueError('Reference view must be your own view of the layout source in this task')
        if view['delivered_at'] is None:
            raise ValueError('Inspect the reference PNG in your next model context before using its anchors')
        metadata = json.loads(view['metadata'])
        area, frame, mapping = metadata['pixel_bounds'], plan['source_frame'], plan['mapping']
        bounds = {'x': mapping['origin'][0] + area['x'] * mapping['scale'],
                  'y': mapping['origin'][1] + area['y'] * mapping['scale'],
                  'width': area['width'] * mapping['scale'], 'height': area['height'] * mapping['scale']}
        mapped = map_observed_arguments(name, arguments, {'scene_bounds': bounds,
            'width': metadata['display_width'], 'height': metadata['display_height'],
            'pixels_per_unit': {'x': metadata['display_width'] / bounds['width'],
                                'y': metadata['display_height'] / bounds['height']}})
        paths = [path['points'] for path in arguments['paths']] if name == 'draw_paths' else [arguments['points']]
        for path in paths:
            for x, y in path:
                sx, sy = area['x'] + x / 1000 * area['width'], area['y'] + y / 1000 * area['height']
                if not (frame['x'] - 1e-8 <= sx <= frame['x'] + frame['width'] + 1e-8
                        and frame['y'] - 1e-8 <= sy <= frame['y'] + frame['height'] + 1e-8):
                    raise ValueError('Reference anchors must stay inside the common source_frame')
        evidence = {'reference_layout_id': arguments['reference_layout_id'],
                    'reference_view_id': arguments['reference_view_id'], 'reference_id': plan['reference_id'],
                    'source_view_bounds': area, 'source_frame': frame, 'target': plan['target'], 'mapping': mapping,
                    'input_space': 'reference_image_normalized', 'output_space': 'canvas'}
        return mapped, {'coordinate_frame': evidence}
