import json


DRAWING_WRITES = (
    'draw_paths', 'reshape_path', 'erase_strokes', 'delete_elements', 'move_elements',
    'update_elements', 'reorder_elements', 'create_element', 'create_elements',
    'trace_reference', 'continue_reference_trace',
)


def stroke_method(arguments):
    paths = arguments.get('paths', [])
    if not paths and 'points' in arguments:
        paths = [arguments]
    return {
        'separate_strokes': len(paths),
        'coordinate_space': arguments.get('coordinate_space', 'canvas'),
        'image_id': arguments.get('observation_id') or arguments.get('reference_view_id'),
        'examples': [
            {'name': path.get('name', ''), 'continuous_points': len(path['points']),
             'span': [round(max(p[axis] for p in path['points']) - min(p[axis] for p in path['points']), 4)
                      for axis in (0, 1)],
             'strokeWidth': path.get('strokeWidth', arguments.get('strokeWidth', 0.4)),
             'opacity': path.get('opacity', arguments.get('opacity', 100))}
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
    live_ids = {item['id'] for item in run['draft'] if not item.get('isDeleted')}
    actions = []
    for call in reversed(calls):
        output = json.loads(call['output'])
        changed = output.get('changed_ids', [])
        actions.append({
            'tool': call['tool_name'], 'call_id': call['call_id'], 'memory_ref': call['id'],
            'action_count': output.get('action_count', 0), 'changed_count': len(changed),
            'current_object_ids': [item for item in changed if item in live_ids][:8],
            'removed_object_ids': [item for item in changed if item not in live_ids][:8],
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
    for message in reversed(messages):
        content = message.get('content')
        if message.get('role') == 'user' and isinstance(content, str) and '"working_memory"' in content:
            payload = json.loads(content)
            if 'working_memory' in payload:
                saved = payload['working_memory']
                break
    tag = f"drawing_input={run['input_id']}"
    current_notes = tag in (saved.get('notes') or {}).get('constraints', [])
    return {'role': 'user', 'content': json.dumps({'drawing_execution_memory': {
        'task_id': run['owner_task_id'], 'agent_id': run['agent_id'], 'input_id': run['input_id'],
        'run_id': run['id'], 'recent_actual_actions': actions, 'delivered_comparisons': comparisons,
        'saved_step': {'revision': saved['revision'], 'memory_ref': saved.get('memory_ref'),
            'tag_matches_current_input': current_notes, 'current_input_tag': tag},
        'construction_step': {
            'target': 'The assigned form/connection and exact current IDs or actual image region.',
            'evidence': 'Reference/layout/landmarks actually inspected; distinguish picture-left from anatomical side.',
            'structure': 'Endpoints, major masses, relative proportions, contacts, occlusion and visible contour breaks.',
            'method': 'Ordered native operations that will change that structure; use separate paths for separate marks.',
            'protect': 'Existing useful forms, highlights, openings and neighboring attachments that must survive.',
            'visible_check': 'A concrete change to inspect in the refreshed close-up AND the whole image.',
        },
        'instruction': 'Continue THIS input from the actual current drawing. This journal is rebuilt from its SQLite '
            'receipts after history compaction or restart; executed actions are not proof of visual success. '
            'The original assignment and current review remain complete elsewhere in this request. '
            'Turn the assigned goal into the construction_step above before extended drawing. '
            'Save the current step with the EXISTING compact_context tool: use saved_step.revision as expected_revision, '
            'retain the current goal, put target/evidence/structure/method/protect/visible_check in summary, '
            'copy current_input_tag into constraints, record verified progress in completed and all unfinished work in pending. '
            'Use only present native object IDs in object_ids and actual memory_ref values in evidence_refs. '
            'Carry forward constraints and pending work when replacing notes. Update notes when a step is verified or '
            'its method fails; do not rewrite an unchanged plan on every tool call. A save may share a response with '
            'a related action when the current evidence already resolves its parameters. '
            'Notes without this input tag are historical: verify any reusable work in the current draft before carrying it forward. '
            'Read previous comparisons as observations plus suggested methods. Verify their object attribution against '
            'current IDs, the complete composition and actual images; report a genuine missing construction prerequisite. '
            'A failed method needs a changed construction, not the same geometry under new names. '
            'For hatching, each short mark is a separate path; do not connect alternating marks into one tall zigzag. '
            'For tonal work choose a bounded shadow region, preserve its adjacent light/contours, and build enough '
            'overlapping marks to make that region visibly darker before adding strand/texture detail. '
            'For connections establish the two attachment points and which contours are hidden before texture. '
            'Exact property edits preserve every unrequested property; research-only inputs prepare no drawing. '
            'Use attached current images for verification; repeat a query only to resolve a stated missing fact. '
            'Keep original tool errors and unresolved work explicit. Tools and continued drawing remain available; '
            'this journal sets no time, action or round limit and does not change the frozen completion review.',
    }}, ensure_ascii=False)}
