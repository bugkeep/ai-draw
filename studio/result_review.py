import json

from .actions import apply_action, matches_fingerprint
from .agent_tools import ViewAgentResult
from .private_workspace import PrivateWorkspaceContext, workspace_descendant, workspace_heads
from .store import VersionConflict


class AgentResultReview:
    def __init__(self, store, vision):
        self.store = store
        self.vision = vision
        self.private_workspaces = PrivateWorkspaceContext(store)

    def repair_context(self, task_id, result_id):
        task, result = self._result(task_id, result_id)
        if result['task_id'] != task_id or not result['run_id'] or result['committed_count'] >= len(result['actions']):
            return None
        run = self.store.agents.get_run(task['session_id'], result['run_id'])
        completion = self.store.agents.run_summary(run).get('completion') or {}
        if completion.get('reason') != 'ineffective_repair':
            return None
        document = self.store.get_document(self.store.session_document_id(task['session_id']))
        shared = {item['id'] for item in document['elements'] if not item.get('isDeleted')}
        visible = {item['id'] for item in run['draft'] if not item.get('isDeleted')}
        findings = completion.get('findings', [])
        targets = {key for finding in findings for key in finding.get('object_ids', [])}
        private_targets = sorted((targets & visible) - shared)
        if not private_targets:
            return None
        focus = findings[0]
        focus_targets = sorted(set(focus.get('object_ids', [])) & visible)
        return {'result_id': result['id'], 'run_id': result['run_id'], 'agent_id': result['agent_id'],
            'targets_present_only_in_pending_draft': private_targets,
            'continue_with': {'tool': 'send_input', 'agent_id': result['agent_id'],
                              'relevant_ids': focus_targets},
            'next_input_scope': {**focus, 'object_ids': focus_targets},
            'remaining_finding_count': len(findings) - 1,
            'next_strategy': completion['next_strategy'],
            'instruction': 'This is an UNFINISHED private candidate. These repair targets do NOT exist on the shared canvas. '
                'To edit them, KEEP this result pending and send ONE targeted correction to this SAME agent; '
                'its next private draft inherits them. Assign only next_input_scope and its visible repair criterion; '
                'apply next_strategy within that scope. Keep the remaining findings with the main Agent for later inputs. '
                'If this scope has no current object IDs, inspect its proposed canvas_region before assigning IDs. '
                'Do not reject_agent_result before editing these IDs. '
                'Rejecting abandons this workspace; a rebuild must then use current shared-canvas IDs '
                'and cannot edit these discarded private IDs.'}

    def _result(self, task_id, result_id):
        task = self.store.get_task(task_id)
        result = self.store.agents.get_result(task['session_id'], result_id)
        with self.store.lock:
            permitted = self.store.connection.execute(
                'SELECT 1 FROM tasks original JOIN tasks current ON current.id=? '
                'WHERE original.id=? AND original.session_id=current.session_id AND original.rowid<=current.rowid',
                (task_id, result['task_id']),
            ).fetchone()
        if not permitted:
            raise ValueError('Drawing result belongs to a later task')
        if result['rejected_reason']:
            raise ValueError('This drawing result has been rejected')
        return task, result

    @staticmethod
    def caller_id(task_id, result):
        return f"main:{task_id}:result:{result['id']}:{result['committed_count']}"

    def proposed(self, task_id, result_id):
        with self.store.lock:
            task, result = self._result(task_id, result_id)
            document = self.store.get_document(self.store.session_document_id(task['session_id']))
            offset = result['committed_count']
            if offset >= len(result['actions']):
                raise ValueError('Drawing result has no unsubmitted actions')
            self.store.agents.validate_commit(document['id'], result_id, offset)
        elements, selection = document['elements'], document['selection']
        for item in result['actions'][offset:]:
            by_id = {element['id']: element for element in elements if not element.get('isDeleted')}
            for element_id, expected in item['expected_elements'].items():
                element = by_id.get(element_id)
                matches = element is not None and (matches_fingerprint(element, expected['fingerprint'])
                    if isinstance(expected, dict) else int(element.get('version', 1)) == expected)
                if not matches:
                    raise VersionConflict(f'Element {element_id} changed while the Agent was drawing')
            elements, selection, _ = apply_action(elements, selection, item['action'])
        return {**document, 'elements': elements, 'selection': selection}, result

    async def observe(self, task_id, result_id, region=None):
        proposed, result = self.proposed(task_id, result_id)
        metadata = await self.vision.observe(task_id, 'main', self.caller_id(task_id, result), proposed,
                                            region, result_id=result_id)
        return {**metadata, 'result': self.store.agents.result_summary(result),
                'selected_ids': proposed['selection'],
                'instruction': 'The proposed result image enters your next context before submission. '
                    'It replays only pending actions on the current shared canvas. Approve if suitable, '
                    'or reject with a concrete defect. Manual edits or partial submission require a fresh preview.'}

    async def context(self, task_id):
        task = self.store.get_task(task_id)
        pending = self.store.agents.pending_results(task['session_id'], task_id=task_id)
        with self.store.lock:
            request = self.store.connection.execute(
                "SELECT arguments,created_at FROM agent_tool_calls WHERE task_id=? AND caller_id=? AND name='view_agent_result' "
                "AND output IS NOT NULL ORDER BY rowid DESC LIMIT 1",
                (task_id, f'main:{task_id}'),
            ).fetchone()
        arguments = {}
        if request:
            try:
                arguments = ViewAgentResult.model_validate_json(request['arguments']).model_dump()
            except ValueError:
                request = None
        requested = arguments.get('result_id')
        candidates = [item['id'] for item in workspace_heads(pending)]
        descendant = workspace_descendant(requested, pending) if requested else None
        if descendant and request['created_at'] < descendant['created_at']:
            requested = None
        if requested:
            try:
                _, result = self._result(task_id, requested)
            except (KeyError, ValueError):
                result = None
            if result and result['committed_count'] < len(result['actions']):
                candidates = [requested, *[result_id for result_id in candidates if result_id != requested]]
            elif result and descendant:
                candidates = [descendant['id'], *[result_id for result_id in candidates if result_id != descendant['id']]]
        errors = []
        for result_id in candidates:
            try:
                proposed, result = self.proposed(task_id, result_id)
            except (KeyError, ValueError, VersionConflict) as error:
                errors.append({'result_id': result_id, 'error': str(error)})
                result = next((item for item in pending if item['id'] == result_id), None)
                if result and result['depends_on']:
                    snapshot = self.private_workspaces.context(task_id, result, errors)
                    if snapshot:
                        repair = self.repair_context(task_id, result_id)
                        if repair:
                            snapshot.append({'role': 'user', 'content': json.dumps(
                                {'private_repair_workspace': repair}, ensure_ascii=False)})
                        return snapshot, {}
                continue
            caller_id = self.caller_id(task_id, result)
            if result_id == requested and arguments.get('region'):
                await self.vision.observe(task_id, 'main', caller_id, proposed, arguments['region'], result_id=result_id)
            messages, observations = await self.vision.context(task_id, 'main', caller_id, proposed, result_id=result_id)
            heading = {'proposed_result': self.store.agents.result_summary(result),
                       'base_document_version': proposed['version'], 'unavailable_previews': errors,
                       'proposed_selection': {'count': len(proposed['selection']), 'ids': proposed['selection'][:12],
                                              'has_more': len(proposed['selection']) > 12},
                       'proposed_visible_objects': sum(not item.get('isDeleted') for item in proposed['elements']),
                       'instruction': 'This candidate combines the current canvas with only this result\'s pending actions. '
                           'It is separate from the actual shared canvas image. Other pending results need their own preview; '
                           'call view_agent_result to choose another result or a close-up.'}
            descendant = workspace_descendant(result_id, pending)
            if descendant:
                heading['newer_private_workspace_result_id'] = descendant['id']
                heading['instruction'] += (
                    ' This explicitly requested image depicts an older dependency. It does not depict the latest '
                    'private workspace. Its old repair findings and object IDs must not be reused for current repairs; '
                    'use the newer private result and current child findings.')
            repair = self.repair_context(task_id, result_id) if not descendant else None
            if repair:
                for message in messages:
                    for part in message['content']:
                        if part['type'] == 'text':
                            content = json.loads(part['text'])
                            content['instruction'] = (
                                'UNFINISHED PROPOSED RESULT: these pending actions are not submitted or approved. '
                                'For a scoped correction, keep this workspace and send input to the same child. '
                                'Follow private_repair_workspace after these pictures. Reject only to abandon this '
                                'workspace and rebuild from current shared-canvas objects. '
                                'Canvas coordinates are given by scene_bounds.')
                            part['text'] = json.dumps(content, ensure_ascii=False)
            continuation = [{'role': 'user', 'content': json.dumps(
                {'private_repair_workspace': repair}, ensure_ascii=False)}] if repair else []
            return [{'role': 'user', 'content': json.dumps(heading, ensure_ascii=False)},
                    *messages, *continuation], {caller_id: observations}
        return ([{'role': 'user', 'content': json.dumps({'result_preview_errors': errors}, ensure_ascii=False)}]
                if errors else []), {}

    def require_review(self, task_id, result_id):
        _, result = self._result(task_id, result_id)
        if result['agent_id'] is None or result['committed_count'] >= len(result['actions']):
            return
        proposed, result = self.proposed(task_id, result_id)
        try:
            self.vision.require_review(task_id, self.caller_id(task_id, result), proposed, allow_empty=False)
        except ValueError as error:
            raise ValueError('Inspect this proposed child result in the next context before applying it; '
                'use view_agent_result for another result or a close-up. The shared canvas or pending actions '
                'changed since any earlier preview.') from error

    def delivered(self, task_id, deliveries):
        for caller_id, observations in deliveries.items():
            self.vision.delivered(task_id, caller_id, observations)
