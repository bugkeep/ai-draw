import base64
import hashlib
import json
import secrets
import time
import uuid

from .actions import create_native_element, element_fingerprint
from .coordinates import map_observed_arguments
from .image_guide import coordinate_guide
from .renderer import CanvasRenderer


def scene_digest(document: dict) -> str:
    content = [(element_fingerprint(item), item.get('seed')) for item in document['elements']
               if not item.get('isDeleted')]
    return hashlib.sha256(json.dumps(content).encode()).hexdigest()


class CanvasVision:
    def __init__(self, store):
        self.store = store
        self.renderer = CanvasRenderer()
        store.connection.executescript('''
            CREATE TABLE IF NOT EXISTS canvas_observations (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                caller_id TEXT NOT NULL, agent_id TEXT NOT NULL,
                scene_hash TEXT NOT NULL, region TEXT NOT NULL,
                metadata TEXT NOT NULL, png BLOB NOT NULL,
                created_at REAL NOT NULL, delivered_at REAL,
                UNIQUE(task_id,caller_id,scene_hash,region)
            );
            CREATE INDEX IF NOT EXISTS idx_observations_owner
                ON canvas_observations(task_id,caller_id,created_at);
            CREATE TABLE IF NOT EXISTS canvas_viewports (
                task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                caller_id TEXT NOT NULL, region TEXT NOT NULL,
                PRIMARY KEY(task_id,caller_id)
            );
        ''')
        store.connection.commit()

    def _check(self, task_id: str, document: dict) -> str:
        session_id = self.store.get_task(task_id)['session_id']
        if self.store.session_document_id(session_id) != document['id']:
            raise ValueError('Canvas observation belongs to another session')
        return session_id

    async def connection_image(self) -> tuple[bytes, dict]:
        kinds = [('ellipse', 'circle'), ('rectangle', 'rectangle'), ('diamond', 'diamond')]
        colors = [('#2563eb', 'blue'), ('#ef4444', 'red'), ('#22c55e', 'green')]
        kind, shape_name = secrets.choice(kinds)
        color, color_name = secrets.choice(colors)
        others = [item for item in kinds if item[0] != kind]
        second_color = secrets.choice([item for item in colors if item[0] != color])[0]
        elements = [create_native_element({'type': kind, 'x': 40, 'y': 40, 'width': 100, 'height': 100,
                                           'backgroundColor': color}),
                    create_native_element({'type': secrets.choice(others)[0], 'x': 230, 'y': 40,
                                           'width': 100, 'height': 100, 'backgroundColor': second_color})]
        rendered = await self.renderer.render({'elements': elements})
        return rendered['png'], {'value': 'draw-agent', 'left_shape': shape_name, 'left_color': color_name}

    async def observe(self, task_id: str, agent_id: str, caller_id: str, document: dict,
                      region: dict | None = None, *, result_id: str | None = None,
                      remember_region: bool = False) -> dict:
        session_id = self._check(task_id, document)
        digest = scene_digest(document)
        region_json = json.dumps(region, sort_keys=True, separators=(',', ':'))
        with self.store.lock:
            cached = self.store.connection.execute(
                'SELECT metadata FROM canvas_observations WHERE task_id=? AND caller_id=? AND scene_hash=? AND region=?',
                (task_id, caller_id, digest, region_json),
            ).fetchone()
        if cached:
            if remember_region:
                self._remember_region(task_id, caller_id, region_json)
            return {**json.loads(cached['metadata']), 'cached': True}
        started = time.perf_counter()
        self.store.publish(document['id'], 'phase', {'task_id': task_id, 'agent_id': agent_id,
                                                   'phase': 'observing'})
        rendered = await self.renderer.render(document, region)
        observation_id = 'view_' + uuid.uuid4().hex
        metadata = {'ok': True, 'observation_id': observation_id, 'scene_hash': digest,
                    'document_id': document['id'], 'document_version': document['version'],
                    'scope': 'proposed_result' if result_id else 'shared' if agent_id == 'main' else 'private_draft',
                    **({'result_id': result_id} if result_id else {}),
                    'region': region,
                    'width': rendered['width'], 'height': rendered['height'],
                    'scene_bounds': rendered['scene_bounds'], 'pixels_per_unit': rendered['pixels_per_unit'],
                    'latency_ms': round((time.perf_counter() - started) * 1000, 1),
                    'instruction': 'The actual PNG is attached to your next model context. Inspect it before completing or correcting the drawing. '
                        'For visual edits use this observation_id with coordinate_space=image_normalized: '
                        'both image axes span 0..1000, independent of PNG size. Runtime converts to canvas units. '
                        'After changes use the fresh current image ID.'}
        with self.store.lock, self.store.connection:
            self.store.connection.execute('INSERT INTO canvas_observations VALUES(?,?,?,?,?,?,?,?,?,?,NULL)',
                (observation_id, session_id, task_id, caller_id, agent_id, digest, region_json,
                 json.dumps(metadata, ensure_ascii=False), rendered['png'], time.time()))
        self.store.log_tool(task_id, agent_id, 0, 'canvas_observation', metadata)
        if remember_region:
            self._remember_region(task_id, caller_id, region_json)
        return metadata

    def _remember_region(self, task_id: str, caller_id: str, region_json: str) -> None:
        with self.store.lock, self.store.connection:
            self.store.connection.execute('INSERT INTO canvas_viewports VALUES(?,?,?) '
                'ON CONFLICT(task_id,caller_id) DO UPDATE SET region=excluded.region',
                (task_id, caller_id, region_json))

    async def context(self, task_id: str, agent_id: str, caller_id: str, document: dict,
                      *, result_id: str | None = None) -> tuple[list[dict], list[str]]:
        self._check(task_id, document)
        with self.store.lock:
            prior = self.store.connection.execute(
                'SELECT 1 FROM canvas_observations WHERE task_id=? AND caller_id=? LIMIT 1',
                (task_id, caller_id),
            ).fetchone()
        if not result_id and not prior and not any(not item.get('isDeleted') for item in document['elements']):
            return [], []
        full = await self.observe(task_id, agent_id, caller_id, document, result_id=result_id)
        with self.store.lock:
            viewport = self.store.connection.execute(
                'SELECT region FROM canvas_viewports WHERE task_id=? AND caller_id=?', (task_id, caller_id),
            ).fetchone()
        if viewport is not None and not result_id:
            focused = json.loads(viewport['region'])
            closeup = await self.observe(task_id, agent_id, caller_id, document, focused) if focused is not None else full
            with self.store.lock:
                rows = self.store.connection.execute('SELECT id,metadata,png FROM canvas_observations '
                    'WHERE task_id=? AND caller_id=? AND scene_hash=? AND id IN (?,?) ORDER BY created_at',
                    (task_id, caller_id, full['scene_hash'], full['observation_id'], closeup['observation_id']),
                ).fetchall()
        else:
            with self.store.lock:
                rows = self.store.connection.execute(
                    'SELECT id,metadata,png FROM canvas_observations WHERE task_id=? AND caller_id=? AND scene_hash=? '
                    'AND (id=? OR id=(SELECT id FROM canvas_observations WHERE task_id=? AND caller_id=? '
                    "AND scene_hash=? AND region!='null' ORDER BY created_at DESC LIMIT 1)) ORDER BY created_at",
                    (task_id, caller_id, full['scene_hash'], full['observation_id'], task_id, caller_id, full['scene_hash']),
                ).fetchall()
        messages, observations = [], []
        for row in rows:
            metadata = json.loads(row['metadata'])
            png = row['png']
            if metadata['region'] is not None and not result_id:
                png = coordinate_guide(png, units='image_normalized')
                metadata['coordinate_guide'] = {
                    'units': 'image_normalized', 'grid_spacing': 100,
                    'instruction': 'Blue grid labels use 0..1000 on EACH axis, not original pixels. '
                        'Use observation_id plus coordinate_space=image_normalized for these anchors. '
                        'The runtime guide is not drawn artwork and does not change PNG dimensions.'}
            messages.append({'role': 'user', 'content': [
                {'type': 'text', 'text': json.dumps({'canvas_observation': metadata,
                    'instruction': ('PROPOSED RESULT: this image replays the pending result on the current shared canvas. '
                        'These actions are NOT submitted yet. Check composition, overlaps, proportions and preservation of existing work. '
                        'Apply this result only if the visible change is suitable; otherwise reject it with a concrete reason. '
                        'Canvas coordinates are given by scene_bounds.' if result_id else
                        'This is the current rendered drawing, not a reference image. Check the visible structure, overlaps and proportions. '
                        'Canvas coordinates are given by scene_bounds.')}, ensure_ascii=False)},
                {'type': 'image_url', 'image_url': {
                    'url': 'data:image/png;base64,' + base64.b64encode(png).decode('ascii'), 'detail': 'high'}},
            ]})
            observations.append(row['id'])
        return messages, observations

    def delivered(self, task_id: str, caller_id: str, observation_ids: list[str]) -> None:
        with self.store.lock, self.store.connection:
            for observation_id in observation_ids:
                changed = self.store.connection.execute(
                    'UPDATE canvas_observations SET delivered_at=? WHERE id=? AND task_id=? AND caller_id=?',
                    (time.time(), observation_id, task_id, caller_id),
                )
                if changed.rowcount != 1:
                    raise ValueError('Canvas image delivery targets another Agent or task')

    def ground_tool(self, task_id: str, caller_id: str, document: dict,
                    name: str, arguments: dict) -> tuple[dict, dict]:
        """Resolve only the caller's delivered, current canvas image; never a proposal."""
        observation_id = arguments.get('observation_id')
        if not observation_id:
            return arguments, {}
        session_id = self._check(task_id, document)
        with self.store.lock:
            row = self.store.connection.execute(
                'SELECT scene_hash,metadata,delivered_at FROM canvas_observations '
                'WHERE id=? AND task_id=? AND caller_id=? AND session_id=?',
                (observation_id, task_id, caller_id, session_id),
            ).fetchone()
        if row is None:
            raise ValueError('The observation belongs to another Agent/task or does not exist. '
                             'Use the view_ ID attached to your own current canvas image.')
        metadata = json.loads(row['metadata'])
        if row['delivered_at'] is None:
            raise ValueError('The observed PNG has not reached your model context yet. '
                             'Inspect it in your next context before using its pixel coordinates.')
        if metadata['scope'] == 'proposed_result':
            raise ValueError('A proposed result is not the current drawing. Use your current shared/private canvas image.')
        if row['scene_hash'] != scene_digest(document):
            raise ValueError('The drawing changed after this observed PNG. Use the fresh view_ ID '
                             'and pixels attached to your next context, then retry the intended edit.')
        mapped = map_observed_arguments(name, arguments, metadata)
        evidence = {key: metadata[key] for key in ('observation_id', 'scene_hash', 'scene_bounds', 'pixels_per_unit')}
        evidence.update(input_space=arguments.get('coordinate_space') or 'image_pixels', output_space='canvas')
        return mapped, {'coordinate_frame': evidence}

    def require_review(self, task_id: str, caller_id: str, document: dict, *, allow_empty: bool = True) -> None:
        self._check(task_id, document)
        if allow_empty and not any(not item.get('isDeleted') for item in document['elements']):
            with self.store.lock:
                prior = self.store.connection.execute(
                    'SELECT 1 FROM canvas_observations WHERE task_id=? AND caller_id=? LIMIT 1',
                    (task_id, caller_id),
                ).fetchone()
            if not prior:
                return
        with self.store.lock:
            row = self.store.connection.execute(
                'SELECT id FROM canvas_observations WHERE task_id=? AND caller_id=? AND scene_hash=? '
                "AND region='null' AND delivered_at IS NOT NULL LIMIT 1", (task_id, caller_id, scene_digest(document)),
            ).fetchone()
        if row is None:
            raise ValueError('The drawing changed after your last visual observation. Inspect the actual current image in your next context, correct visible defects, then complete. Use view_canvas for a closer region.')

    def image(self, session_id: str, observation_id: str) -> bytes:
        with self.store.lock:
            row = self.store.connection.execute(
                'SELECT png FROM canvas_observations WHERE id=? AND session_id=?', (observation_id, session_id),
            ).fetchone()
        if row is None:
            raise KeyError('Canvas image does not belong to this session')
        return row['png']

    async def close(self):
        await self.renderer.close()
