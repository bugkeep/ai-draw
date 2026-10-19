import base64
import hashlib
import json
import secrets
import time
import uuid

from .actions import create_native_element, element_fingerprint
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
                      region: dict | None = None) -> dict:
        session_id = self._check(task_id, document)
        digest = scene_digest(document)
        region_json = json.dumps(region, sort_keys=True, separators=(',', ':'))
        with self.store.lock:
            cached = self.store.connection.execute(
                'SELECT metadata FROM canvas_observations WHERE task_id=? AND caller_id=? AND scene_hash=? AND region=?',
                (task_id, caller_id, digest, region_json),
            ).fetchone()
        if cached:
            return {**json.loads(cached['metadata']), 'cached': True}
        started = time.perf_counter()
        self.store.publish(document['id'], 'phase', {'task_id': task_id, 'agent_id': agent_id,
                                                   'phase': 'observing'})
        rendered = await self.renderer.render(document, region)
        observation_id = 'view_' + uuid.uuid4().hex
        metadata = {'ok': True, 'observation_id': observation_id, 'scene_hash': digest,
                    'document_id': document['id'], 'document_version': document['version'],
                    'scope': 'shared' if agent_id == 'main' else 'private_draft',
                    'region': region,
                    'width': rendered['width'], 'height': rendered['height'],
                    'scene_bounds': rendered['scene_bounds'], 'pixels_per_unit': rendered['pixels_per_unit'],
                    'latency_ms': round((time.perf_counter() - started) * 1000, 1),
                    'instruction': 'The actual PNG is attached to your next model context. Inspect it before completing or correcting the drawing.'}
        with self.store.lock, self.store.connection:
            self.store.connection.execute('INSERT INTO canvas_observations VALUES(?,?,?,?,?,?,?,?,?,?,NULL)',
                (observation_id, session_id, task_id, caller_id, agent_id, digest, region_json,
                 json.dumps(metadata, ensure_ascii=False), rendered['png'], time.time()))
        self.store.log_tool(task_id, agent_id, 0, 'canvas_observation', metadata)
        return metadata

    async def context(self, task_id: str, agent_id: str, caller_id: str, document: dict) -> tuple[list[dict], list[str]]:
        self._check(task_id, document)
        with self.store.lock:
            prior = self.store.connection.execute(
                'SELECT 1 FROM canvas_observations WHERE task_id=? AND caller_id=? LIMIT 1',
                (task_id, caller_id),
            ).fetchone()
        if not prior and not any(not item.get('isDeleted') for item in document['elements']):
            return [], []
        full = await self.observe(task_id, agent_id, caller_id, document)
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
            messages.append({'role': 'user', 'content': [
                {'type': 'text', 'text': json.dumps({'canvas_observation': metadata,
                    'instruction': 'This is the current rendered drawing, not a reference image. Check the visible structure, overlaps and proportions. Canvas coordinates are given by scene_bounds.'}, ensure_ascii=False)},
                {'type': 'image_url', 'image_url': {
                    'url': 'data:image/png;base64,' + base64.b64encode(row['png']).decode('ascii'), 'detail': 'high'}},
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

    def require_review(self, task_id: str, caller_id: str, document: dict) -> None:
        self._check(task_id, document)
        if not any(not item.get('isDeleted') for item in document['elements']):
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
