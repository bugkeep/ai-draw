import json

from .eraser import filled_path
from .child_art_context import appearance_index


DRAWING_WRITES = (
    'draw_paths', 'reshape_path', 'erase_strokes', 'delete_elements', 'move_elements',
    'update_elements', 'reorder_elements', 'create_element', 'create_elements',
    'trace_reference', 'continue_reference_trace',
)


def stroke_method(arguments):
    paths = arguments.get('paths', [])
    inherited = 'element_id' in arguments
    if not paths and 'points' in arguments:
        paths = [arguments]
    return {
        'separate_strokes': len(paths),
        'coordinate_space': arguments.get('coordinate_space', 'canvas'),
        'image_id': arguments.get('observation_id') or arguments.get('reference_view_id'),
        'examples': [
            {'name': path.get('name', ''), 'continuous_points': len(path['points']),
             'smooth': path.get('smooth', True),
             'span': [round(max(p[axis] for p in path['points']) - min(p[axis] for p in path['points']), 4)
                      for axis in (0, 1)],
             **{key: path[key] if path.get(key) is not None else arguments.get(key,
                    'inherit_current_object' if inherited else default)
                for key, default in (('strokeWidth', 0.4), ('opacity', 100),
                                     ('closed', False), ('backgroundColor', 'transparent'))}}
            for path in paths[:4] if path.get('points')
        ],
        'interpretation': 'Each path is ONE continuous pen stroke; its points do not lift the pen. '
            'span uses the declared coordinate space, not necessarily canvas units. '
            'Names describe intent and do not prove a visible structure or technique.',
    }


def drawing_memory_context(memory, run, messages):
    store = memory.store
    with store.lock:
        owner = store.connection.execute('SELECT r.owner_task_id,i.agent_id,r.input_id FROM agent_runs r '
            'JOIN agent_inputs i ON i.id=r.input_id WHERE r.id=?',
            (run['id'],)).fetchone()
        if owner is None or tuple(owner) != (run['owner_task_id'], run['agent_id'], run['input_id']):
            raise ValueError('Drawing memory requires the exact current child input')
        calls = store.connection.execute(
            'SELECT m.id,m.tool_name,c.call_id,c.arguments,c.output FROM agent_memories m '
            'JOIN agent_tool_calls c ON c.caller_id=m.caller_id AND c.call_id=m.call_id '
            "WHERE m.caller_id=? AND m.task_id=? AND m.actor_id=? AND m.is_error=0 AND json_extract(c.output,'$.ok')=1 "
            'AND m.tool_name IN (SELECT value FROM json_each(?)) ORDER BY m.created_at DESC,m.rowid DESC LIMIT 3',
            (run['id'], run['owner_task_id'], run['agent_id'], json.dumps(DRAWING_WRITES)),
        ).fetchall()
        reviews = store.connection.execute(
            'SELECT result,scene_hash FROM draft_reviews WHERE run_id=? AND task_id=? AND observed_at IS NOT NULL '
            "AND json_extract(result,'$.status') IN ('repair','ready') ORDER BY created_at DESC LIMIT 3",
            (run['id'], run['owner_task_id']),
        ).fetchall()
        current_reads = store.connection.execute(
            'SELECT count(*) FROM agent_memories WHERE caller_id=? AND task_id=? AND actor_id=? '
            'AND state_key=? AND is_error=0 AND observed_at IS NOT NULL '
            "AND tool_name IN ('view_canvas','inspect_scene','recall_memory','view_reference_image')",
            (run['id'], run['owner_task_id'], run['agent_id'], f"{run['id']}:{len(run['prepared'])}"),
        ).fetchone()[0]
    live_ids = {item['id'] for item in run['draft'] if not item.get('isDeleted')}
    current_objects = {item['id']: item for item in run['draft'] if not item.get('isDeleted')}
    actions = []
    for call in reversed(calls):
        output = json.loads(call['output'])
        changed = output.get('changed_ids', [])
        actions.append({
            'tool': call['tool_name'], 'call_id': call['call_id'], 'memory_ref': call['id'],
            'action_count': output.get('action_count', 0), 'changed_count': len(changed),
            'current_object_ids': [item for item in changed if item in live_ids][:8],
            'removed_object_ids': [item for item in changed if item not in live_ids][:8],
            **({'executed_displacement': {key: json.loads(call['arguments'])[key] for key in ('dx', 'dy')},
                'current_bounds_after_all_actions': [{'id': key,
                    'bounds': [current_objects[key][field] for field in ('x', 'y', 'width', 'height')]}
                    for key in changed if key in current_objects]}
               if call['tool_name'] == 'move_elements' else {}),
            **({'stroke_method': stroke_method(json.loads(call['arguments']))}
               if call['tool_name'] in {'draw_paths', 'reshape_path'} else {}),
        })
    comparisons = []
    for row in reversed(reviews):
        review = json.loads(row['result'])
        revision = review.get('assessment', {}).get('revision') or {}
        comparisons.append({
            'review_key': review['review_key'], 'reviewed_scene_hash': row['scene_hash'],
            'status': review['status'], 'change': revision.get('change'),
            'observed_result': revision.get('evidence', '')[:600],
            'suggested_method': revision.get('next_strategy', '')[:600],
        })
    saved = {'revision': 0, 'notes': None}
    saved_found = False
    current_review = {}
    for message in reversed(messages):
        content = message.get('content')
        if (message.get('role') == 'user' and isinstance(content, str)
                and ('"working_memory"' in content or '"current_private_draft_review"' in content)):
            payload = json.loads(content)
            if 'working_memory' in payload and not saved_found:
                saved = payload['working_memory']
                saved_found = True
            if 'current_private_draft_review' in payload and not current_review:
                current_review = payload['current_private_draft_review']
    findings = current_review.get('assessment', {}).get('findings', []) if current_review.get('status') == 'repair' else []
    focus = findings[0] if findings else None
    tag = f"drawing_input={run['input_id']}"
    current_notes = tag in (saved.get('notes') or {}).get('constraints', [])
    with store.lock:
        saved_here = store.connection.execute('SELECT 1 FROM agent_tool_calls '
            "WHERE caller_id=? AND task_id=? AND name='compact_context' AND json_extract(output,'$.ok')=1 "
            "AND json_extract(output,'$.memory_ref')=? LIMIT 1",
            (run['id'], run['owner_task_id'], saved.get('memory_ref'))).fetchone() is not None
    priority = set(run['selection']) | set(run['relevant_ids']) | set((focus or {}).get('object_ids', []))
    filled = []
    for position, element in enumerate(item for item in run['draft'] if not item.get('isDeleted')):
        if (element.get('backgroundColor') not in {None, '', 'transparent'}
                and (element['type'] in {'rectangle', 'ellipse', 'diamond'} or filled_path(element))):
            filled.append({'id': element['id'], 'name': element.get('customData', {}).get('name', ''),
                'type': element['type'], 'position_in_current_draft': position,
                'native_bounds': [element['x'], element['y'], element['width'], element['height']],
                'angle': element.get('angle', 0), 'backgroundColor': element['backgroundColor'],
                'strokeWidth': element.get('strokeWidth'), 'opacity': element.get('opacity', 100)})
    shown_fills = sorted(sorted(filled, key=lambda item: (item['id'] in priority,
        item['position_in_current_draft']), reverse=True)[:24], key=lambda item: item['position_in_current_draft'])
    object_index = [{'id': item['id'], 'name': item.get('customData', {}).get('name', ''), 'type': item['type'],
        'bounds': [item[field] for field in ('x', 'y', 'width', 'height')], 'angle': item.get('angle', 0),
        'version': item.get('version', 1)}
        for item in run['draft'] if not item.get('isDeleted')]
    shown_index = object_index if len(object_index) <= 120 else [item for item in object_index
        if item['id'] in priority or any(item['id'] in action['current_object_ids'] for action in actions)]
    shown_index, styles = appearance_index(shown_index, current_objects)
    return {'role': 'user', 'content': json.dumps({'drawing_execution_memory': {
        'task_id': run['owner_task_id'], 'agent_id': run['agent_id'], 'input_id': run['input_id'],
        'run_id': run['id'], 'recent_actual_actions': actions, 'delivered_comparisons': comparisons,
        'pending_review_steps': ({'review_key': prior['review_key'], 'reviewed_scene_hash': reviews[0]['scene_hash'],
            'assessment': prior['assessment'],
            'instruction': 'This review describes an EARLIER draft, before subsequent actions. Verify each finding '
                'against current bounds/images; completed repairs are not fresh defects. Keep the remaining assigned '
                'steps pending. After the current correction is visible, proceed to the other required forms.'}
            if not current_review and reviews and (prior := json.loads(reviews[0]['result']))['status'] == 'repair' else None),
        'current_object_index': {'total_count': len(object_index), 'shown_count': len(shown_index),
            'objects': shown_index, 'styles': styles,
            'instruction': 'Exact live native IDs and names in this private draft. style_id resolves to THIS index\'s native styles table; '
                'persist selected style properties, not temporary style_id numbers. '
                'bounds are CURRENT native [x,y,width,height] after ALL executed actions; angle is separate. '
                'The immutable DrawSpec contains planned targets, not updated object coordinates after edits. '
                'Use current bounds and executed displacements to compute a correction to the intended contact/plane. '
                'current_polyline is a compact read-only view of current path centerline samples in CANVAS coordinates, '
                'after translation/rotation, with at most one canvas unit simplification error. It is not original '
                'curve-control anchors or proof of visibility. Dense curves are omitted; retrieve their current points '
                'with recall_memory when needed. Compare geometric claims with these actual samples and the picture '
                'before redrawing an already correct contour. '
                'A successful move already changed the native positions; verify the visible relation before another move. '
                'Use these receipts for selection and report bindings; names are intent, not visual evidence. '
                'For omitted objects use a targeted inspect_scene page or recall_memory. Query once for '
                'the missing identity/geometry; reuse its result until the relevant objects change.'},
        'current_filled_objects': {'total_count': len(filled), 'shown_count': len(shown_fills), 'objects': shown_fills,
            'order': 'Current native draft array, back to front; larger positions are later/front.',
            'instruction': 'These are current filled objects, not logs or proof of a visibility defect. '
                'An opaque foreground fill can hide earlier details. For an assigned occlusion correction, '
                'check the actual image, opacity and layer order before erasing or redrawing a correct contour. '
                'Use existing reorder_elements when order is the actual defect; preserve unrequested stacking in exact edits. '
                'Native bounds and angle describe the object, not a clipped visible region. '
                'Shown entries prefer current targets and frontmost fills; use inspect_scene/recall_memory for other objects.'},
        'next_step_focus': ({'review_key': current_review.get('review_key'),
            'reported_defect': focus['defect'], 'canvas_region': focus.get('canvas_region'),
            'current_object_ids': [item for item in focus.get('object_ids', []) if item in live_ids],
            'removed_object_ids': [item for item in focus.get('object_ids', []) if item not in live_ids],
            'authority': 'A scoped review observation to verify against the immutable plan and actual image.'}
            if focus else None),
        'work_phase': {'last_successful_operation': actions[-1]['tool'] if actions else None,
            'cleanup_was_last_operation': bool(actions and actions[-1]['tool'] in {'erase_strokes', 'delete_elements'}
                and actions[-1]['action_count']),
            'delivered_evidence_reads_at_current_prepared_count': current_reads,
            'instruction': 'These are execution facts, not completion claims or call limits. '
                'When the assignment requires rebuilding, cleanup leaves that construction pending. '
                'Use the already attached image and source/landmarks to reconstruct the required contour next. '
                'For an erasure-only request, preserve that scope and do not add a replacement.'},
        'saved_step': {'revision': saved['revision'], 'memory_ref': saved.get('memory_ref'),
            'tag_matches_current_input': current_notes, 'created_in_current_input': saved_here,
            'current_input_tag': tag},
        'construction_step': {
            'target': 'The assigned form/connection and exact current IDs or actual image region.',
            'evidence': 'Reference/layout/landmarks actually inspected; distinguish picture-left from anatomical side.',
            'structure': 'Endpoints, major masses, relative proportions, contacts, occlusion and visible contour breaks.',
            'method': 'The NEXT tool, exact current targets or grounded anchors, contour start/turn/end and hidden intervals; '
                'use separate paths for separate marks.',
            'protect': 'Existing useful forms, highlights, openings and neighboring attachments that must survive.',
            'visible_check': 'A concrete change to inspect in the refreshed close-up AND the whole image.',
        },
        'instruction': 'Continue THIS input from the current drawing and immutable assignment. Receipts prove execution, '
            'not visual success. Build coherent units with explicit proportions, contacts, occlusion and contour stops; '
            'batch related forms with known parameters, including a whole stage block-in. After cleanup, complete the '
            'saved reconstruction before reporting. Use the attached refreshed images to check the visible result. '
            'Query only a stated missing fact; reuse retrieved IDs/geometry while those objects remain unchanged. '
            'For uncertain anatomy inspect the source detail with its actual layout mapping. '
            'Choose native rectangle/ellipse/text for geometric frames, ellipses and lettering. For straight architectural '
            'edges or angular polygons set smooth=false on EACH draw_paths path. draw_paths/reshape_path default '
            'smooth=true and bend multi-anchor contours; reserve this for intended curves. Organic contours need ordered '
            'extrema and turning anchors. Each hatch mark is a separate path; establish shadow masses before texture. '
            'Create opaque backgrounds/masks BEFORE foreground details/text; use reorder_elements for actual layer defects. '
            'In canvas coordinates OMIT optional observation_id/coordinate_space/reference_view_id/reference_layout_id; '
            'never send the string "null". Image coordinates require the actual matching image ID and enum. '
            'Use at most one canvas-image-bound mutation per response, batching its paths/targets; the next request refreshes '
            'the mapping. A note save may accompany an already grounded action. '
            'Save durable progress or a changed strategy through compact_context, without a planning-only save round when '
            'the next action is known: expected_revision=saved_step.revision; summary contains the construction_step; '
            'constraints retain prior constraints plus current_input_tag; completed contains verified results; pending retains '
            'all unfinished work. object_ids contains live native IDs. evidence_refs contains actual memory_ references '
            'from tool receipts; omit it when none is available. Image view_, reference_, result_ and agent_ IDs are '
            'not evidence_refs. created_in_current_input is verified '
            'from SQLite call ownership; use it to distinguish current notes from historical notes, even if a written tag differs. '
            'An unchanged step needs no new save. Preserve errors and genuine missing prerequisites. '
            'Review checkpoints are candidate reports, verification of a constructed repair, and stagnation/error recovery. '
            'For a repair, view_canvas WITHOUT a region requests whole-pass verification after construction. '
            'A regional view is a close-up for your next drawing step; current whole and close-up pictures are already '
            'attached after changes. Reuse them while the state/region is unchanged. Cleanup that removes required '
            'structure leaves reconstruction pending. Erasing only the actual unwanted segments can complete a '
            'correction without replacement strokes; request whole-pass verification or return the candidate report. '
            'On repair, continue this input '
            'with a changed method where required. When the assigned pass meets its criteria, return the existing DrawSpec '
            'report: concise visible evidence, exact current IDs from the index/receipts, representative bindings for every '
            'required form, explicit unresolved work. A ready review of the same state needs that report. '
            'Exact edits preserve unrequested properties; research prepares no drawing. No time/action/round limit is imposed.',
    }}, ensure_ascii=False)}
