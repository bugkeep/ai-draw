import json

from .drawing_dsl import assignment, latest_spec
from .vision import scene_digest


def constraint_forms(plan, constraint_id):
    forms = {item['id'] for item in plan['forms']}
    members = {key: {key} for key in forms}
    parents = {item['id']: item['parent_id'] for item in plan['forms']}
    for key in forms:
        parent = parents[key]
        while parent:
            members[parent].add(key)
            parent = parents[parent]
    members.update({item['id']: {form['id'] for form in plan['forms'] if form['subject_id'] == item['id']}
                    for item in plan['subjects']})
    members.update({item['id']: {item['form_id']} for item in plan['landmarks']})
    for stage in plan['stages']:
        if stage['id'] == constraint_id:
            return set(stage['form_ids'] or forms)
    constraint = next(item for item in [*plan['proportions'], *plan['relations']]
                      if item['id'] == constraint_id)
    return members.get(constraint['source'], set()) | members.get(constraint['target'], set())


def completion_progress(store, task_id, document):
    task = store.get_task(task_id)
    if store.session_document_id(task['session_id']) != document['id']:
        raise ValueError('Drawing completion evidence belongs to another canvas')
    current = latest_spec(store, task_id)
    if current is None:
        return None
    with store.lock:
        rows = store.connection.execute(
            'SELECT i.message,i.id input_id,i.agent_id,r.id run_id,r.status run_status,'
            'd.id result_id,d.rejected_reason,d.committed_count,json_array_length(d.actions) action_count,'
            'n.payload completion FROM agent_inputs i JOIN agent_runs r ON r.input_id=i.id '
            'LEFT JOIN drawing_results d ON d.run_id=r.id '
            'LEFT JOIN agent_completion_notes n ON n.run_id=r.id '
            'WHERE i.task_id=? AND r.owner_task_id=? '
            'ORDER BY COALESCE(d.created_at,r.created_at),r.rowid', (task_id, task_id)).fetchall()
    evidence = {'live_ids': [item['id'] for item in document['elements'] if not item.get('isDeleted')],
                'scene_hash': scene_digest(document)}
    return planned_progress(current, [dict(row) for row in rows], evidence)


def completion_context(store, task_id, document, picture_context=()):
    progress = completion_progress(store, task_id, document)
    if progress is None or progress['ok']:
        return list(picture_context)
    delivered = []
    for message in picture_context:
        payload = json.loads(message['content'])
        if (payload.get('current_drawing_review') or {}).get('ok'):
            payload['instruction'] = ('The submitted picture passed its independent image assessment. '
                'The original creation still has required stages or explicit unfinished work, listed in '
                'the current runtime completion state below. Continue that original work through the '
                'existing child tools, keep useful progress, and submit verified results before completing.')
            message = {**message, 'content': json.dumps(payload, ensure_ascii=False)}
        delivered.append(message)
    return [*delivered, {'role': 'user', 'content': json.dumps({'drawing_completion_state': progress,
        'instruction': 'The runtime has verified that this original creation is still unfinished. '
            'The required stages and unresolved work below remain part of the original task. '
            'A positive current_drawing_review is image evidence about the submitted picture; '
            'the task also requires this original plan to be complete. Keep useful pending child '
            'progress, correct its concrete defects through the same child, and submit verified work. '
            'Use the exact remaining criteria below. Completion replies, empty memory pending lists '
            'and abandoning a candidate do not satisfy the original unfinished stages.'}, ensure_ascii=False)}]


def planned_progress(current, rows, evidence):
    requests = [(row, assignment(row['message'])) for row in rows]
    if not any(request and request.drawspec.intent == 'execute' and request.drawspec.phase == 'block_in'
               for _, request in requests):
        return None
    plan = current['drawspec']['composition']
    forms = {item['id'] for item in plan['forms']}
    stages = {item['id']: item for item in plan['stages']}
    order = {key: index for index, key in enumerate(stages)}
    covered = {key: set() for key in stages}
    bindings, unresolved = {}, []
    live, digest = set(evidence['live_ids']), evidence['scene_hash']
    for row, request in requests:
        if (request is None or request.drawspec.composition_id != current['composition_id']
                or request.drawspec.intent == 'research' or row['run_status'] != 'completed'
                or not row['result_id'] or row['rejected_reason']
                or row['committed_count'] != row['action_count']):
            continue
        completion = json.loads(row['completion']) if row['completion'] else {}
        report = completion.get('drawspec') or {}
        spec = request.drawspec
        scope = set(spec.form_ids or stages[spec.stage_id]['form_ids'] or forms)
        checks = {item['constraint_id']: item for item in report.get('checks', [])}
        report_bindings = {item['form_id']: set(item['object_ids']) for item in report.get('bindings', [])}
        # 无动作的检查必须针对当前共享图片，私有候选不能作为已完成证据。
        ready = (completion.get('review_status') == 'ready' and bool(report)
                 and (row['action_count'] > 0 or report.get('scene_hash') == digest))
        bound = scope & report_bindings.keys()
        if ready:
            bindings.update(report_bindings)
            for key in stages:
                check = checks.get(key)
                if check and check['status'] == 'addressed':
                    covered[key].update(bound & set(stages[key]['form_ids'] or forms))
            if (completion.get('status') == 'ready' and not report.get('unresolved')
                    and not any(item['status'] == 'unresolved' for item in checks.values())):
                covered[spec.stage_id].update(bound)
                for issue in unresolved:
                    check = checks.get(issue.get('constraint_id'))
                    addressed = (check and check['status'] == 'addressed') if issue.get('constraint_id') else (
                        order[spec.stage_id] >= order[issue['stage_id']])
                    if addressed:
                        if issue.get('constraint_id') not in stages and issue.get('constraint_id'):
                            if bound & issue['remaining_form_ids']:
                                issue['remaining_form_ids'].clear()
                        else:
                            issue['remaining_form_ids'].difference_update(bound)
        for key, check in checks.items():
            if check['status'] == 'unresolved' and key in stages:
                covered[key].difference_update(scope)
        origin = {'agent_id': row['agent_id'], 'input_id': row['input_id'],
                  'run_id': row['run_id'], 'result_id': row['result_id'], 'stage_id': spec.stage_id}
        texts = list(dict.fromkeys([*completion.get('unresolved', []), *report.get('unresolved', [])]))
        failed_checks = [item for item in checks.values() if item['status'] == 'unresolved']
        for text in texts:
            if not any(text == check['evidence'] for check in failed_checks):
                unresolved.append({**origin, 'evidence': text, 'remaining_form_ids': set(scope)})
        for check in failed_checks:
            unresolved.append({**origin, 'constraint_id': check['constraint_id'],
                'evidence': check['evidence'],
                'remaining_form_ids': constraint_forms(plan, check['constraint_id'])})
    missing_bindings = {key for key in forms if not bindings.get(key, set()) & live}
    pending = []
    for key, stage in stages.items():
        missing = (set(stage['form_ids'] or forms) - covered[key]) | (
            missing_bindings & set(stage['form_ids'] or forms))
        if missing:
            pending.append({'stage_id': key, 'phase': stage['phase'],
                'remaining_form_ids': sorted(missing), 'criteria': stage['criteria']})
    issues = [{**item, 'remaining_form_ids': sorted(item['remaining_form_ids'])}
              for item in unresolved if item['remaining_form_ids']]
    return {'ok': not pending and not issues, 'composition_ref': current['composition_id'],
        'composition_revision': plan['revision'], 'pending_stages': pending, 'unresolved_reports': issues,
        'finish_criteria': plan['art_direction']['finish_criteria'],
        'stage_checks_are_artist_claims': True, 'independent_whole_picture_review_required': True,
        **({'error_kind': 'drawing_plan_incomplete',
            'instruction': 'Continue the explicit unfinished drawing plan using the existing child tools. '
                'Complete or verify the earliest unfinished stage before advancing to decorative detail. '
                'Preserve completed native objects and reuse the same artist when continuing its work. '
                'Resolve the original composition proportions, perspective, contacts and occlusions before '
                'decorative detail. Pass the exact remaining stage criteria and report evidence to the artist. '
                'Several scoped inputs may complete one stage; a report may explicitly check several stages '
                'already visible in the current drawing. Inspect and report existing finished work without '
                'redrawing it. A stage name, object count or whole-picture approval does not clear explicit '
                'unfinished work. Keep unresolved constraints explicit. Independent whole-picture review '
                'remains necessary after the plan is complete.'} if pending or issues else {})}
