import base64
import json

from .vision import scene_digest


def workspace_heads(pending):
    covered = {key for result in pending for key in result['depends_on']}
    return [result for result in pending if result['id'] not in covered]


def workspace_descendant(result_id, pending):
    by_id = {result['id']: result for result in pending}
    for head in reversed(workspace_heads(pending)):
        remaining, visited = list(head['depends_on']), set()
        while remaining:
            key = remaining.pop()
            if key == result_id:
                return head
            if key not in visited:
                visited.add(key)
                remaining.extend(by_id.get(key, {}).get('depends_on', []))
    return None


class PrivateWorkspaceContext:
    def __init__(self, store):
        self.store = store

    def context(self, task_id, result, preview_errors):
        task = self.store.get_task(task_id)
        if result['task_id'] != task_id:
            raise ValueError('Private workspace belongs to another task')
        if not result['run_id']:
            return []
        run = self.store.agents.get_run(task['session_id'], result['run_id'])
        if run['owner_task_id'] != task_id or run['status'] != 'completed':
            return []
        document = self.store.get_document(self.store.session_document_id(task['session_id']))
        digest = scene_digest({**run['document'], 'elements': run['draft'], 'selection': run['selection']})
        with self.store.lock:
            rows = self.store.connection.execute('SELECT id,metadata,png FROM canvas_observations '
                'WHERE task_id=? AND session_id=? AND caller_id=? AND scene_hash=? AND delivered_at IS NOT NULL '
                "AND json_extract(metadata,'$.scope')='private_draft' AND (region='null' OR id=("
                'SELECT id FROM canvas_observations WHERE task_id=? AND caller_id=? AND scene_hash=? '
                "AND region!='null' AND delivered_at IS NOT NULL ORDER BY created_at DESC LIMIT 1)) "
                "ORDER BY region='null' DESC", (task_id, task['session_id'], run['id'], digest,
                    task_id, run['id'], digest)).fetchall()
        if not any(json.loads(row['metadata'])['region'] is None for row in rows):
            return []
        heading = {'pending_private_workspace': self.store.agents.result_summary(result),
            'snapshot_base_document_version': run['document']['version'], 'shared_document_version': document['version'],
            'shared_canvas_changed': run['document']['version'] != document['version'],
            'native_submission_preview_available': False, 'unavailable_previews': preview_errors,
            'instruction': 'The saved latest private draft includes this result and its pending dependencies. '
                'Use it as read-only evidence for planning a focused repair. It is not a replay on the current '
                'shared canvas and does not approve any submission. Shared-canvas changes after its base version '
                'are not pictured. Native dependency order, conflict checks and per-result visual review still apply. '
                'Use view_agent_result to explicitly inspect a dependency before submitting it. '
                'Do not use the child observation IDs with main canvas-editing tools.'}
        messages = [{'role': 'user', 'content': json.dumps(heading, ensure_ascii=False)}]
        for row in rows:
            metadata = json.loads(row['metadata'])
            evidence = {key: metadata[key] for key in ('observation_id', 'scene_hash', 'region', 'scene_bounds', 'width', 'height')}
            evidence.update(result_id=result['id'], run_id=run['id'], scope='saved_private_workspace', read_only=True)
            messages.append({'role': 'user', 'content': [
                {'type': 'text', 'text': json.dumps({'private_workspace_image': evidence,
                    'instruction': 'READ-ONLY SAVED PRIVATE DRAFT: inspect the actual pixels for the current '
                        'pending workspace. This child image cannot satisfy main submission review or ground '
                        'main canvas edits. The child uses its own fresh images for subsequent mutations.'}, ensure_ascii=False)},
                {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' +
                    base64.b64encode(row['png']).decode('ascii'), 'detail': 'high'}}]})
        return messages
