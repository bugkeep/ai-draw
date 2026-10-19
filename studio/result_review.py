import json

from .actions import apply_action, matches_fingerprint
from .store import VersionConflict


class AgentResultReview:
    def __init__(self, store, vision):
        self.store = store
        self.vision = vision

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
                "SELECT arguments FROM agent_tool_calls WHERE task_id=? AND caller_id=? AND name='view_agent_result' "
                "AND output IS NOT NULL AND json_extract(output,'$.ok')=1 ORDER BY created_at DESC LIMIT 1",
                (task_id, f'main:{task_id}'),
            ).fetchone()
        arguments = json.loads(request['arguments']) if request else {}
        requested = arguments.get('result_id')
        candidates = [item['id'] for item in pending]
        if requested:
            try:
                _, result = self._result(task_id, requested)
            except (KeyError, ValueError):
                result = None
            if result and result['committed_count'] < len(result['actions']):
                candidates = [requested, *[result_id for result_id in candidates if result_id != requested]]
        errors = []
        for result_id in candidates:
            try:
                proposed, result = self.proposed(task_id, result_id)
            except (KeyError, ValueError, VersionConflict) as error:
                errors.append({'result_id': result_id, 'error': str(error)})
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
            return [{'role': 'user', 'content': json.dumps(heading, ensure_ascii=False)}, *messages], {caller_id: observations}
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
