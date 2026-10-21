import json
from collections import Counter

from .vision import scene_digest


def compact_object_index(index):
    if 'object_defaults' in index:
        return index
    result = {**index, 'objects': [{**item} for item in index['objects']]}
    defaults = {}
    for key in ('type', 'angle', 'version', 'text', 'style_id'):
        if not result['objects'] or any(key not in item for item in result['objects']):
            continue
        encoded, count = Counter(json.dumps(item[key], ensure_ascii=False)
                                 for item in result['objects']).most_common(1)[0]
        value = json.loads(encoded)
        if count <= 1:
            continue
        defaults[key] = value
        for item in result['objects']:
            if json.dumps(item[key], ensure_ascii=False) == encoded:
                del item[key]
    if defaults:
        result['object_defaults'] = defaults
    polyline_defaults = {'coordinate_space': 'canvas', 'max_centerline_error': 1}
    used_polyline_defaults = False
    polylines = [item['current_polyline'] for item in result['objects'] if item.get('current_polyline') is not None]
    complete_metadata = all(all(key in item for key in polyline_defaults) for item in polylines)
    for item in result['objects']:
        original = item.get('current_polyline')
        if original is None or not complete_metadata:
            continue
        polyline = dict(original)
        for key, value in polyline_defaults.items():
            if key in polyline and polyline[key] == value:
                del polyline[key]
                used_polyline_defaults = True
        item['current_polyline'] = polyline
    if used_polyline_defaults:
        result['polyline_defaults'] = polyline_defaults
    if defaults or used_polyline_defaults:
        result['encoding'] = ('For omitted object fields use object_defaults; for omitted current_polyline fields '
            'use polyline_defaults. Explicit fields override defaults. All coordinates retain their full precision.')
    return result if len(json.dumps(result, ensure_ascii=False)) < len(json.dumps(index, ensure_ascii=False)) else index


def reuse_original_guidance(prompt):
    brief = prompt.get('art_execution')
    if brief is None or 'original_fields' in brief:
        return
    spec = prompt['drawspec']
    stages = spec['composition']['stages']
    position = next(index for index, item in enumerate(stages) if item['id'] == spec['stage_id'])
    fields = {
        'stage_criteria': ['drawspec', 'composition', 'stages', position, 'criteria'],
        'success_criteria': ['drawspec', 'success_criteria'],
        'direction': ['drawspec', 'composition', 'art_direction'],
        'value_design': ['drawspec', 'composition', 'value_groups'],
    }
    references = {}
    for key, path in fields.items():
        value = prompt
        for part in path:
            value = value[part]
        if (brief.get(key) == value and len(json.dumps(value, ensure_ascii=False)) >
                len(json.dumps(path, ensure_ascii=False))):
            references[key] = path
            del brief[key]
    if references:
        brief['original_fields'] = references
        brief['original_fields_instruction'] = ('These fields use the complete original values already present '
            'at the listed key/index paths in this SAME assignment. Read that original direction and criteria '
            'together with the painting_sequence and decisions below; no standards or constraints are removed.')


def visual_evidence(run, messages):
    digest = scene_digest({'elements': run['draft']})
    images = []
    for message in messages:
        blocks = message.get('content')
        if message.get('role') != 'user' or not isinstance(blocks, list):
            continue
        for position, block in enumerate(blocks[:-1]):
            if (block.get('type') != 'text' or not block['text'].startswith('{"canvas_observation":')
                    or blocks[position + 1].get('type') != 'image_url'):
                continue
            observation = json.loads(block['text'])['canvas_observation']
            if (observation['scene_hash'] == digest and observation['scope'] == 'private_draft'
                    and observation['document_id'] == run['document']['id']):
                images.append({key: observation[key] for key in ('observation_id', 'region')})
    return {'scene_hash': digest, 'attached_current_images': images,
        'full_current_image_attached': any(item['region'] is None for item in images),
        'instruction': ('Inspect these attached current PNGs. Request a close-up only for an unresolved visual question. '
            'An unchanged whole view adds no pixels; after a constructed repair it can explicitly request independent '
            'repair verification. Keep that checkpoint when needed. After a ready review, return the supported report. '
            'For the ASSIGNED forms, check each declared stage against its original criteria using the current '
            'picture and live object bindings, including earlier stages after old bindings were replaced. '
            'Report supported stage checks and retain unresolved criteria. Never infer a stage from its order '
            'or claim forms outside your assignment. Attachment alone proves no visual inspection or completion.' if images else
            'No current PNG is attached. Construct the assigned work and inspect the refreshed picture in the next '
            'request before claiming its visible criteria are met.')}


def project_execution_context(run, prompt, messages):
    reuse_original_guidance(prompt)
    evidence = visual_evidence(run, messages)
    projected = list(messages)
    last = messages[-1].get('content')
    if (messages[-1].get('role') == 'user' and isinstance(last, str)
            and last.startswith('{"drawing_execution_memory":')):
        payload = json.loads(last)
        journal = payload['drawing_execution_memory']
        journal['current_object_index'] = compact_object_index(journal['current_object_index'])
        prompt.pop('current_visual_evidence', None)
        journal['current_visual_evidence'] = evidence
        journal['native_eraser_coordinates'] = (
            'current_object_index current_polyline.points are current CANVAS centerline samples. '
            'For an erase_strokes gesture at a known sample, copy that pair into points and use the intended '
            'live ids; OMIT observation_id and coordinate_space. Radius then uses native canvas units. '
            'Choose stroke mode only when the entire object is unwanted; segment mode preserves other parts. '
            'Independent edits with known canvas coordinates may be batched. For an edit chosen from pixels, '
            'use an attached CURRENT image ID and its actual mapping; after an image-bound mutation wait '
            'for the refreshed image before another image-bound mutation. After clipping, use the returned '
            'current fragment IDs. Old image IDs in historical calls are not current drawing evidence.')
        if 'drawing_report' in prompt:
            report = prompt.pop('drawing_report')
            stages = prompt['drawspec']['composition']['stages']
            report['current_stage_id'] = prompt['drawspec']['stage_id']
            position = next(index for index, item in enumerate(stages) if item['id'] == report['current_stage_id'])
            report['later_stage_ids'] = [item['id'] for item in stages[position + 1:]]
            report['instruction'] += (
                ' Each checks.constraint_id must appear only ONCE. Combine evidence for that constraint '
                'across the assigned forms in one check; keep status unresolved when a required part remains '
                'unresolved. Use exactly the fields shown in format; add no extra check fields. '
                'Finish repairable work in current_stage_id with tools before returning. Planned later_stage_ids '
                'belong in summary, NOT in unresolved or unresolved checks merely because they are not started. '
                'They remain pending in the original plan. Preserve genuine defects, missing prerequisites and '
                'conflicting constraints of THIS assignment honestly; never omit them to obtain a ready result.')
            journal['drawing_report'] = report
            prompt['drawing_report_reference'] = 'drawing_execution_memory.drawing_report at the END of this input'
        projected[-1] = {**messages[-1], 'content': json.dumps(payload, ensure_ascii=False)}
    else:
        prompt['current_visual_evidence'] = evidence
    projected[1] = {**messages[1], 'content': json.dumps(prompt, ensure_ascii=False)}
    return projected
