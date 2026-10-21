import json
import re
from collections import Counter

from .drawing_dsl import DrawingReport
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


def report_binding_repair(run, messages, index):
    error = None
    for message in reversed(messages):
        if message.get('role') == 'assistant':
            if error is None or message.get('tool_calls'):
                return None
            content = json.loads(message['content'])
            if 'rejected_drawing_report_memory' in content:
                bindings = [{'form_id': binding['form_id'],
                    'object_ids': binding['current_object_ids'],
                    'unavailable_reference_count': binding['unavailable_reference_count']}
                    for binding in content['rejected_drawing_report_memory']['reference_bindings']]
            else:
                bindings = [binding.model_dump() for binding in DrawingReport.model_validate(content).bindings]
            break
        content = message.get('content')
        if (error is None and message.get('role') == 'user' and isinstance(content, str)
                and content.startswith('{') and '"drawing_protocol"' in content):
            payload = json.loads(content)
            if (payload.get('error_kind') == 'drawing_protocol'
                    and payload.get('error') == 'Report object_ids must exist in this exact private draft'):
                error = payload
    else:
        return None
    live = {item['id']: item for item in run['draft'] if not item.get('isDeleted')}
    shown = {item['id'] for item in index['objects']}
    invalid = []
    for binding in bindings:
        absent_count = sum(key not in live for key in binding['object_ids']) + binding.get('unavailable_reference_count', 0)
        if not absent_count:
            continue
        present = [key for key in binding['object_ids'] if key in live]
        invalid.append({'form_id': binding['form_id'], 'unavailable_reference_count': absent_count,
            'still_live_binding_ids': present,
            'current_name_matches': [{'id': item['id'], 'name': item.get('customData', {}).get('name', ''),
                'binding_candidate': {'form_id': binding['form_id'], 'object_ids': [*present, item['id']]}}
                for item in live.values() if item['id'] in shown and item['id'] not in binding['object_ids']
                and binding['form_id'].casefold() in str(item.get('customData', {}).get('name', '')).casefold()]})
    if not invalid:
        return None
    return {'run_id': run['id'], 'invalid_bindings': invalid,
        'instruction': 'Resolve each absent reference using the actual current form and its COMPLETE native ID. '
            'current_name_matches are literal name matches among unbound objects in the shown current index. '
            'Confirm the form in the current picture or retrieve its native geometry when needed. '
            'Preserve every required form binding and genuine unresolved work. If a form was actually removed, '
            'reconstruct it before reporting. A binding_candidate combines the unchanged live parts with ONE '
            'literal name match; it is a candidate for verification, not an approved association. With multiple '
            'absent parts, resolve them all before reporting. Copy verified bindings with their complete native '
            'IDs. Do not copy unavailable IDs from rejected historical reports.'}


def rejected_report_memories(run, messages):
    live = {item['id'] for item in run['draft'] if not item.get('isDeleted')}
    projected = list(messages)
    assistants = [position for position, message in enumerate(messages) if message.get('role') == 'assistant']
    latest = assistants[-1] if assistants else None
    previous = None
    for position, message in enumerate(messages):
        if message.get('role') == 'assistant':
            previous = position
            continue
        content = message.get('content')
        if (previous is None or messages[previous].get('tool_calls')
                or message.get('role') != 'user' or not isinstance(content, str)
                or not content.startswith('{') or '"drawing_protocol"' not in content):
            continue
        error = json.loads(content)
        if (error.get('error_kind') != 'drawing_protocol'
                or error.get('error') != 'Report object_ids must exist in this exact private draft'):
            continue
        original = json.loads(messages[previous]['content'])
        if 'rejected_drawing_report_memory' in original:
            continue
        report = DrawingReport.model_validate(original)
        memory = {'summary': report.summary, 'unresolved': report.unresolved,
            'unresolved_checks': [check.model_dump() for check in report.checks if check.status == 'unresolved'],
            'reported_form_ids': [binding.form_id for binding in report.bindings],
            'unavailable_reference_count': sum(key not in live for binding in report.bindings for key in binding.object_ids)}
        memory['status'] = 'rejected_report_claims'
        if previous == latest:
            memory['checks'] = [check.model_dump() for check in report.checks]
            memory['reference_bindings'] = [{'form_id': binding.form_id,
                'current_object_ids': [key for key in binding.object_ids if key in live],
                'unavailable_reference_count': sum(key not in live for key in binding.object_ids)}
                for binding in report.bindings]
        memory['instruction'] = (
            'This historical report was rejected. Its claims do not establish completion. '
            'Unavailable IDs are omitted from this memory; use the current index and latest repair evidence. '
            'The original report remains in SQLite. Preserve its unresolved work and current assignment.')
        projected[previous] = {**messages[previous], 'content': json.dumps(
            {'rejected_drawing_report_memory': memory}, ensure_ascii=False)}
    return projected


def latest_failed_exchange(messages):
    for position in range(len(messages) - 1, -1, -1):
        message = messages[position]
        if message.get('role') != 'assistant':
            continue
        calls = message.get('tool_calls') or []
        if not calls:
            return None
        end = position + 1
        while end < len(messages) and messages[end].get('role') == 'tool':
            end += 1
        outputs = messages[position + 1:end]
        if (len(calls) != len(outputs) or len({call['id'] for call in calls}) != len(calls)
                or {call['id'] for call in calls} != {item.get('tool_call_id') for item in outputs}):
            return None
        failures = {item['tool_call_id']: json.loads(item['content']) for item in outputs
            if json.loads(item['content']).get('ok') is False}
        return {'start': position, 'calls': calls, 'failures': failures} if failures else None
    return None


def tool_argument_repairs(messages):
    exchange = latest_failed_exchange(messages)
    if exchange is None:
        return []

    def unused(value):
        return value is None or isinstance(value, str) and value.strip().casefold() in {'', 'null', 'none', 'undefined'}

    repairs = []
    for call in exchange['calls']:
        name = call['function']['name']
        if call['id'] not in exchange['failures'] or name not in {'draw_paths', 'reshape_path', 'erase_strokes'}:
            continue
        try:
            arguments = json.loads(call['function']['arguments'])
        except json.JSONDecodeError:
            continue
        if not isinstance(arguments, dict):
            continue
        bindings = ('observation_id', 'reference_view_id', 'reference_layout_id')
        native = all(unused(arguments.get(key)) for key in bindings) and (
            unused(arguments.get('coordinate_space')) or arguments.get('coordinate_space') == 'canvas')
        omitted = [key for key in (*bindings, 'coordinate_space') if native and key in arguments]
        if (isinstance(arguments.get('observation_id'), str)
                and re.fullmatch(r'view_[a-f0-9]{32}', arguments['observation_id'])
                and arguments.get('coordinate_space') in {'image_pixels', 'image_normalized'}
                and all(unused(arguments.get(key)) for key in bindings[1:])):
            omitted = [key for key in bindings[1:] if key in arguments]
        per_path = ['smooth'] if name == 'draw_paths' and 'smooth' in arguments else []
        if omitted or per_path:
            repairs.append({'call_id': call['id'], 'tool': name, 'omit_unused_optional_fields': omitted,
                'fields_belonging_to_each_path': per_path,
                'instruction': 'Syntax guidance for this actual rejected call. Omit the listed unused binding fields '
                    'entirely; native canvas coordinates use no image/reference binding. For image/source work keep '
                    'the actual current view IDs and their matching coordinate space. Listed per-path fields belong '
                    'inside each paths entry, not at the call root. Preserve the intended targets and geometry while '
                    'correcting the call. This hint executes nothing and does not approve any drawing change.'})
    return repairs


def project_execution_context(run, prompt, messages):
    reuse_original_guidance(prompt)
    evidence = visual_evidence(run, messages)
    projected = rejected_report_memories(run, messages)
    last = messages[-1].get('content')
    if (messages[-1].get('role') == 'user' and isinstance(last, str)
            and last.startswith('{"drawing_execution_memory":')):
        payload = json.loads(last)
        journal = payload['drawing_execution_memory']
        journal['current_object_index'] = compact_object_index(journal['current_object_index'])
        prompt.pop('current_visual_evidence', None)
        journal['current_visual_evidence'] = evidence
        binding_repair = report_binding_repair(run, messages[:-1], journal['current_object_index'])
        journal.pop('report_binding_repair', None)
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
        if binding_repair is not None:
            journal['report_binding_repair'] = binding_repair
        repairs = tool_argument_repairs(messages)
        journal.pop('tool_argument_repairs', None)
        if repairs:
            journal['tool_argument_repairs'] = repairs
        projected[-1] = {**messages[-1], 'content': json.dumps(payload, ensure_ascii=False)}
    else:
        prompt['current_visual_evidence'] = evidence
    projected[1] = {**messages[1], 'content': json.dumps(prompt, ensure_ascii=False)}
    return projected
