import argparse
import asyncio
import json
import sqlite3
import time
from pathlib import Path

from dotenv import load_dotenv

from studio.llm import close_model_clients
from studio.runtime import DrawingRuntime
from studio.settings import ModelSettings
from studio.store import StudioStore
from .evaluate_draft_loop import save_draft
from .evaluation_artifacts import capture_implementation


async def evaluate(args):
    if args.database.exists() or args.output_dir.exists():
        raise ValueError('Choose new isolated evaluation paths')
    source = args.source_database.resolve()
    with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True) as archived:
        archived.row_factory = sqlite3.Row
        scope = archived.execute('SELECT r.owner_task_id,t.session_id,n.payload FROM agent_runs r '
            'JOIN tasks t ON t.id=r.owner_task_id JOIN agent_completion_notes n ON n.run_id=r.id WHERE r.id=?',
            (args.run_id,)).fetchone()
        if scope is None or json.loads(scope['payload']).get('reason') != 'ineffective_repair':
            raise ValueError('Select a saved genuine ineffective-repair handoff')
        args.database.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(args.database) as copied:
            archived.backup(copied)
        scope = dict(scope)
    args.output_dir.mkdir(parents=True)
    implementation = capture_implementation(args.output_dir)
    store = StudioStore(args.database)
    runtime = DrawingRuntime(store, ModelSettings(store))
    started = time.time()
    task_id, session_id = scope['owner_task_id'], scope['session_id']
    report = {'source_database': str(source), 'source_opened_read_only': True,
        'database': str(args.database), 'run_id': args.run_id, 'task_id': task_id, 'implementation': implementation,
        'started_at': started,
        'scope': 'genuine main Agent reception and reassignment after an unfinished child; not whole-art acceptance',
        'artwork_acceptance': False}
    (args.output_dir / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    try:
        baseline = runtime.quality.baseline(task_id)
        report['original_request'] = baseline['request']
        before = store.get_document(baseline['document']['id'])
        store.update_task(task_id, 'running', '独立副本：验证主 Agent 读取未完成反馈并重新安排任务。')
        worker = asyncio.create_task(runtime._run(task_id, session_id, before['id'], baseline['request']))
        runtime.running[task_id] = worker
        while True:
            assignment = store.connection.execute('SELECT call_id,name,arguments,output FROM agent_tool_calls '
                "WHERE caller_id=? AND created_at>=? AND name IN ('send_input','spawn_agent') "
                "AND output IS NOT NULL AND coalesce(json_extract(output,'$.ok'),1)=1 "
                "AND json_extract(output,'$.input_id') IS NOT NULL ORDER BY created_at LIMIT 1",
                (f'main:{task_id}', started)).fetchone()
            if assignment:
                report['new_assignment'] = {**dict(assignment), 'arguments': json.loads(assignment['arguments']),
                                            'output': json.loads(assignment['output'])}
                report['stopped_because'] = 'main_saved_new_child_input'
                break
            if worker.done():
                await worker
                report['stopped_because'] = 'main_reached_terminal_state'
                break
            if (args.output_dir / 'stop.json').exists():
                report['stopped_because'] = 'operator_requested_stop'
                break
            await asyncio.sleep(0.25)
        if not worker.done():
            runtime.cancel(task_id)
            await asyncio.gather(worker, return_exceptions=True)
        logs = [row for row in store.tool_logs(task_id) if row['created_at'] >= started]
        contexts = [row['payload'] for row in logs if row['agent_id'] == 'main' and row['kind'] == 'model_context']
        after = store.get_document(before['id'])
        report['checks'] = {
            'handoff_reaches_actual_main_context': any(args.run_id in row.get('replan_run_ids', []) for row in contexts),
            'original_whole_request_retained': baseline['request'] == runtime.quality.baseline(task_id)['request'],
            'new_input_saved': bool(report.get('new_assignment')),
            'only_main_controls_submission': all(row['agent_id'] == 'main' for row in logs
                if row['kind'] == 'function_call' and row['payload']['name'] == 'apply_agent_result'),
        }
        run = store.agents.get_run(session_id, args.run_id)
        report.update(elapsed_ms=round((time.time() - started) * 1000, 1), task=store.get_task(task_id),
            before_version=before['version'], after_version=after['version'],
            main_contexts=contexts, model_responses=[row['payload'] for row in logs if row['agent_id'] == 'main'
                and row['kind'] == 'llm_response'],
            private_observation_id=await save_draft(runtime, run, session_id, args.output_dir, 'handoff-draft'),
            passed=all(report['checks'].values()))
        (args.output_dir / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'checks': report['checks'], 'elapsed_ms': report['elapsed_ms'],
            'assignment': report.get('new_assignment', {}).get('arguments'), 'passed': report['passed']}, ensure_ascii=False), flush=True)
        if not report['passed']:
            raise SystemExit(1)
    finally:
        await runtime.close()
        await close_model_clients()
        store.close()


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description='Observe a real main Agent act on a saved child replan handoff.')
    parser.add_argument('--source-database', type=Path, required=True)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    asyncio.run(evaluate(parser.parse_args()))


if __name__ == '__main__':
    main()
