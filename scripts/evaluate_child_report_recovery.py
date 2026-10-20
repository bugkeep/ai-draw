import argparse
import asyncio
import json
import sqlite3
import time
from pathlib import Path

from dotenv import load_dotenv

from studio.agents import DrawingAgents
from studio.llm import close_model_clients
from studio.runtime import DrawingRuntime
from studio.settings import ModelSettings
from studio.store import StudioStore
from studio.vision import scene_digest
from .evaluation_artifacts import capture_implementation


def copy_checkpoint(args):
    if args.database.exists() or args.output_dir.exists():
        raise ValueError('Choose new isolated diagnostic paths')
    with sqlite3.connect(args.source_database.resolve().as_uri() + '?mode=ro', uri=True) as source:
        source.row_factory = sqlite3.Row
        response = source.execute("SELECT * FROM tool_logs WHERE id=? AND kind='llm_response'",
            (args.response_log_id,)).fetchone()
        run = source.execute('SELECT r.*,i.agent_id,a.session_id FROM agent_runs r JOIN agent_inputs i ON i.id=r.input_id '
            'JOIN agent_sessions a ON a.id=i.agent_id WHERE r.id=?', (args.run_id,)).fetchone()
        if response is None or run is None or response['agent_id'] != run['agent_id']:
            raise ValueError('Select a recorded response from this run')
        message = json.loads(response['payload'])['message']
        if message.get('tool_calls') or not message.get('content'):
            raise ValueError('Select a recorded no-tools report candidate')
        history = json.loads(run['conversation'])
        index = next((index for index, item in enumerate(history) if item == message), None)
        if index is None:
            raise ValueError('The saved conversation must contain the exact report checkpoint')
        document = json.loads(run['document'])
        document.update(elements=json.loads(run['draft']), selection=json.loads(run['selection']))
        context = source.execute("SELECT payload FROM tool_logs WHERE agent_id=? AND kind='model_context' AND id<? "
            'ORDER BY id DESC LIMIT 1', (run['agent_id'], response['id'])).fetchone()
        observations = json.loads(context['payload'])['image_observation_ids'] if context else []
        actual = source.execute("SELECT 1 FROM canvas_observations WHERE caller_id=? AND scene_hash=? AND region='null' "
            'AND id IN (SELECT value FROM json_each(?))', (run['id'], scene_digest(document), json.dumps(observations))).fetchone()
        if actual is None:
            raise ValueError('The saved native draft must match the actual full image delivered before this response')
        args.database.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(args.database) as target:
            source.backup(target)
    return dict(run), message, history[:index], response['created_at']


async def evaluate(args):
    original, message, history, response_time = copy_checkpoint(args)
    args.output_dir.mkdir(parents=True)
    implementation = capture_implementation(args.output_dir)
    store = StudioStore(args.database)
    runtime = DrawingRuntime(store, ModelSettings(store))
    session_id, task_id, agent_id = original['session_id'], original['owner_task_id'], original['agent_id']
    report = {'diagnostic': 'recover the exact recorded invalid child report on its unchanged native draft',
        'source_database': str(args.source_database), 'source_read_only': True,
        'response_log_id': args.response_log_id, 'source_run_id': original['id'],
        'database': str(args.database), 'implementation_sha256': implementation,
        'artwork_acceptance': False, 'main_agent_started': False}
    started = time.time()
    worker = None
    try:
        with store.lock, store.connection:
            store.connection.execute('DELETE FROM drawing_results WHERE run_id=?', (original['id'],))
            store.connection.execute('DELETE FROM agent_completion_notes WHERE run_id=?', (original['id'],))
            store.connection.execute('DELETE FROM draft_reviews WHERE run_id=? AND created_at>?', (original['id'], response_time))
            store.connection.execute("UPDATE agent_runs SET status='running',pending_response=?,conversation=?,result_id=NULL,"
                "summary='',error='',result_revision=0 WHERE id=?", (store.agents.encode(message), store.agents.encode(history), original['id']))
            store.connection.execute("UPDATE agent_inputs SET status='running' WHERE id=?", (original['input_id'],))
            store.connection.execute("UPDATE agent_sessions SET state='Running',summary='' WHERE id=?", (agent_id,))
        store.update_task(task_id, 'running', '独立副本：从实际回报格式错误继续原输入。')
        run = store.agents.get_run(session_id, original['id'])
        before_digest = scene_digest({**run['document'], 'elements': run['draft'], 'selection': run['selection']})
        report.update(initial_prepared_actions=len(run['prepared']), initial_scene_hash=before_digest,
            original_input_id=run['input_id'], original_agent_id=agent_id)
        models = DrawingAgents(store, runtime.settings.load())
        worker = asyncio.create_task(runtime.children._execute_run(models, session_id, run))
        runtime.children.workers[agent_id] = worker
        while True:
            current = store.agents.get_run(session_id, original['id'])
            pending = store.agents.pending_continuations(session_id, agent_id)
            if pending or current['status'] in {'completed', 'failed'} or worker.done():
                report['stopped_because'] = ('provider_output_truncated' if pending else 'input_terminal')
                break
            await asyncio.sleep(0.2)
        if worker.done():
            worker.result()
    finally:
        await runtime.children.close_agent(session_id, agent_id)
        current = store.agents.get_run(session_id, original['id'])
        logs = [item for item in store.tool_logs(task_id) if item['created_at'] >= started]
        responses = [item['payload'] for item in logs if item['kind'] == 'llm_response' and item['agent_id'] == agent_id]
        document = {**current['document'], 'elements': current['draft'], 'selection': current['selection']}
        summary = store.agents.run_summary(current)
        report.update(elapsed_ms=round((time.time() - started) * 1000, 1), final_prepared_actions=len(current['prepared']),
            final_scene_hash=scene_digest(document), run_summary=summary, live_model_responses=len(responses),
            protocol_errors=[item['payload'] for item in logs if item['kind'] == 'drawing_protocol_error'],
            usage=[item.get('usage') for item in responses],
            draft_unchanged=scene_digest(document) == report.get('initial_scene_hash'),
            prepared_actions_unchanged=len(current['prepared']) == report.get('initial_prepared_actions'),
            model_parameters_unchanged=True)
        report['report_recovery_passed'] = bool(summary.get('completion', {}).get('drawspec')
            and report['draft_unchanged'] and report['prepared_actions_unchanged']
            and current['input_id'] == report['original_input_id'])
        (args.output_dir / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        await runtime.close()
        await close_model_clients()
        store.close()
    return report


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description='Resume a genuine invalid child report in an isolated SQLite copy.')
    parser.add_argument('--source-database', type=Path, required=True)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--response-log-id', type=int, required=True)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    report = asyncio.run(evaluate(parser.parse_args()))
    print(json.dumps({key: report[key] for key in ('report_recovery_passed', 'draft_unchanged', 'live_model_responses',
        'elapsed_ms', 'stopped_because')}, ensure_ascii=False), flush=True)
    if not report['report_recovery_passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
