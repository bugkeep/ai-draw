import json

from .drawing_dsl import assignment
from .vision import scene_digest


def observation_context(messages, references, pictures):
    if [item.get('role') for item in messages[:2]] != ['system', 'user']:
        raise ValueError('Child observations require the role and current assignment prefix')
    return [*messages[:2], *references, *pictures, *messages[2:]]


def report_context(spec):
    stage = next(item for item in spec.composition.stages if item.id == spec.stage_id)
    return {'format': {'protocol': 'drawspec/1', 'kind': 'report', 'summary': '<brief visible result>',
        'bindings': [{'form_id': '<declared form ID>', 'object_ids': ['<current native ID>']}],
        'checks': [{'constraint_id': '<relation/proportion/stage ID>', 'status': 'addressed|unresolved|unchanged',
                    'evidence': '<visible evidence or remaining defect>'}], 'unresolved': ['<unfinished work, or empty array>']},
        'required_form_ids': spec.form_ids or stage.form_ids or [item.id for item in spec.composition.forms],
        'stage_criteria': stage.criteria,
        'allowed_constraint_ids': {
            'proportions': [item.id for item in spec.composition.proportions],
            'relations': [item.id for item in spec.composition.relations],
            'stages': [item.id for item in spec.composition.stages]},
        'instruction': 'When returning without tools, output one valid JSON object with double-quoted keys/strings, '
            'without prose or code fences. For checks.constraint_id, copy an exact ID from allowed_constraint_ids; '
            'use a stage ID for that stage\'s visible criteria. Describe other observations in summary/unresolved. '
            'Include only checks you can support with actual evidence; checks may be empty. '
            'Copy current native object IDs for bindings. Never claim whole-artwork approval. '
            'Include unfinished work explicitly. Your checks are claims for review, not approval. '
            'Runtime supplies identities, composition revision, native result ID and current image evidence.'}


def repair_context(reviews, review, document, replan=None):
    message = reviews.context(review, document)
    if replan:
        content = json.loads(message['content'])
        content['repair_strategy'] = {
            'reason': replan['reason'], 'review_key': replan['review_key'],
            'next_strategy': replan['next_strategy'],
            'instruction': 'The previous method did not solve this defect. Tools remain available. '
                'Use the changed strategy, inspect the whole structure and affected connections, '
                'and continue the current input. Erase or replace incorrect contours before adding detail. '
                'Return unresolved work only for an actual missing prerequisite, conflicting assignment '
                'or when delivering an honest local candidate. An ineffective attempt is not an execution limit.',
        }
        message['content'] = json.dumps(content, ensure_ascii=False)
    return message


def coordinate_context(store, run, observation_ids):
    request = assignment(run['message'])
    if request is None or not observation_ids:
        return None
    spec = request.drawspec
    stage = next(item for item in spec.composition.stages if item.id == spec.stage_id)
    scope = set(spec.form_ids or stage.form_ids or [form.id for form in spec.composition.forms])
    document = {**run['document'], 'elements': run['draft'], 'selection': run['selection']}
    digest = scene_digest(document)
    with store.lock:
        images = store.connection.execute('SELECT id,metadata FROM canvas_observations WHERE task_id=? '
            'AND caller_id=? AND scene_hash=? AND id IN (SELECT value FROM json_each(?))',
            (run['owner_task_id'], run['id'], digest, json.dumps(observation_ids))).fetchall()
    if {row['id'] for row in images} != set(observation_ids):
        raise ValueError('DrawSpec coordinate guides require this input\'s exact current private-draft images')
    frames = []
    for row in images:
        metadata = json.loads(row['metadata'])
        if metadata.get('scope') != 'private_draft':
            raise ValueError('DrawSpec coordinate guides cannot use another workspace or proposed-result image')
        area = metadata['scene_bounds']

        def point(x, y):
            return [round((x - area['x']) / area['width'] * 1000, 4),
                    round((y - area['y']) / area['height'] * 1000, 4)]

        def inside(points):
            return all(0 <= value <= 1000 for position in points for value in position)

        landmarks, forms, outside = [], [], []
        for mark in spec.composition.landmarks:
            if mark.form_id not in scope:
                continue
            image_point = point(mark.x, mark.y)
            if inside([image_point]):
                landmarks.append({'id': mark.id, 'canvas': [mark.x, mark.y], 'image_normalized': image_point})
            else:
                outside.append(mark.id)
        for form in spec.composition.forms:
            if form.id not in scope:
                continue
            bounds = form.bounds
            corners = [point(bounds.x, bounds.y), point(bounds.x + bounds.width, bounds.y + bounds.height)]
            if inside(corners):
                forms.append({'id': form.id, 'image_normalized_corners': corners})
            else:
                outside.append(form.id)
        frames.append({'observation_id': row['id'], 'coordinate_space': 'image_normalized',
            'landmarks': landmarks, 'form_bounds': forms, 'outside_image_use_canvas': outside})
    return {'role': 'user', 'content': json.dumps({'drawspec_coordinate_guide': {
        'composition_id': spec.composition_id, 'stage_id': spec.stage_id, 'scene_hash': digest, 'images': frames,
        'instruction': 'These are the main composition\'s PLANNED TARGETS, computed in each actual attached image. '
            'To draw at a planned canvas point, omit observation_id/coordinate_space and copy its canvas pair. '
            'To use image_normalized, use the matching observation_id and copy that image\'s image_normalized pair; '
            'NEVER copy a canvas pair into image_normalized. Planned form bounds use the same distinction. '
            'Targets outside this PNG require canvas coordinates or a different view; they are not clamped. '
            'Review prose may contain unlabelled coordinates; use these typed targets and actual image metadata '
            'to locate the requested correction. Observe the actual pixels, preserve the current intent and '
            'neighboring connections. These target coordinates do not prove the drawing is correct. '
            'After a drawing change, this image mapping is stale; use the next automatically refreshed guide.'}}, ensure_ascii=False)}
