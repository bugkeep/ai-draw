"""Schedule source views that agree with an assigned drawing layout."""

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ReferenceWorkInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    kind: Literal['reference_guided_repair']
    reference_layout_id: str = Field(pattern=r'^layout_[a-f0-9]{32}$')
    goal: str = Field(min_length=1)


class ReferenceWork:
    def __init__(self, images, task_id, assignment):
        self.images, self.task_id, self.assignment = images, task_id, assignment
        self.layout = images.layouts._record(task_id, assignment.reference_layout_id)
        self.source = json.loads(images._record(task_id, self.layout['reference_id'])['metadata'])

    @classmethod
    def load(cls, images, task_id, message):
        try:
            value = json.loads(message)
        except (ValueError, TypeError):
            return None
        if not isinstance(value, dict) or value.get('kind') != 'reference_guided_repair':
            return None
        return cls(images, task_id, ReferenceWorkInput.model_validate(value))

    def prepare_source(self, caller_id):
        with self.images.store.lock:
            latest = self.images.store.connection.execute('SELECT reference_id,delivered_at FROM reference_image_views '
                'WHERE task_id=? AND caller_id=? ORDER BY created_at DESC LIMIT 1', (self.task_id, caller_id)).fetchone()
            views = self.images.store.connection.execute('SELECT id,region FROM reference_image_views '
                'WHERE task_id=? AND caller_id=? AND reference_id=? ORDER BY created_at',
                (self.task_id, caller_id, self.layout['reference_id'])).fetchall()
        # Honor an explicit view request before scheduling the default source again.
        if latest and latest['reference_id'] != self.layout['reference_id'] and latest['delivered_at'] is None:
            return
        full_missing = not any(view['region'] == 'null' for view in views)
        if full_missing:
            self.images.view(self.task_id, caller_id, {'reference_id': self.layout['reference_id']})
        selected = json.loads(views[-1]['region']) if views else None
        if selected is not None:
            if full_missing or latest['reference_id'] != self.layout['reference_id']:
                self.images.view(self.task_id, caller_id, {'reference_id': self.layout['reference_id'], 'region': selected})
            return
        frame = self.layout['source_frame']
        if frame == {'x': 0, 'y': 0, 'width': self.source['width'], 'height': self.source['height']}:
            return
        self.images.view(self.task_id, caller_id, {'reference_id': self.layout['reference_id'], 'region': frame})
        # Delivery remains the normal runtime receipt after a successful real model request.

    def context(self):
        return {'kind': 'reference_guided_repair', 'goal': self.assignment.goal,
            'reference_layout_id': self.assignment.reference_layout_id,
            'source_frame': self.layout['source_frame'], 'target': self.layout['target'],
            'instruction': 'Use your own layout-cropped source view (or the full source when source_frame is '
                'the entire image). Use that reference_view_id, or a tighter own close-up, with this layout '
                'and image_normalized coordinates: both axes 0..1000 span the ACTUAL viewed PNG. '
                'Keep source anchors inside source_frame; full-page margins are outside the drawing layout. '
                'Repair the assigned part using existing IDs and preserve useful contours. Use current canvas '
                'observations instead for artwork already moved away from the source layout.'}
