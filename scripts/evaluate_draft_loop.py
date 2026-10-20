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
from .evaluation_artifacts import capture_implementation


async def save_draft(runtime, run, session_id, output_dir, label):
    document = {**run['document'], 'elements': run['draft'], 'selection': run['selection']}
    observation = await runtime.vision.observe(run['owner_task_id'], run['agent_id'],
        f"diagnostic-{label}:{run['id']}", document)
    (output_dir / f'{label}.png').write_bytes(runtime.vision.image(session_id, observation['observation_id']))
    (output_dir / f'{label}.excalidraw').write_text(json.dumps({'type': 'excalidraw', 'version': 2,
        'source': 'ai-draw-draft-loop-diagnostic', 'elements': document['elements'],
        'appState': {'viewBackgroundColor': '#ffffff'}, 'files': {}}, ensure_ascii=False), encoding='utf-8')
    return observation['observation_id']


async def evaluate(args):
    if args.database.exists() or args.output_dir.exists():
        raise ValueError('Choose new isolated diagnostic paths')
    source = args.source_database.resolve()
    with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True) as archived:
        archived.row_factory = sqlite3.Row
        selected = archived.execute('SELECT r.owner_task_id,t.session_id,r.status FROM agent_runs r '
            'JOIN tasks t ON t.id=r.owner_task_id WHERE r.id=?', (args.run_id,)).fetchone()
        if selected is None or selected['status'] not in {'running', 'paused'}:
            raise ValueError('Select an unfinished original child run')
        args.database.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(args.database) as copied:
            archived.backup(copied)
        scope = dict(selected)
    args.output_dir.mkdir(parents=True)
    implementation = capture_implementation(args.output_dir)
    store = StudioStore(args.database)
    runtime = DrawingRuntime(store, ModelSettings(store))
    try:
        return await continue_run(args, source, implementation, scope, store, runtime)
    finally:
        await runtime.close()
        await close_model_clients()
        store.close()


async def continue_run(args, source, implementation, scope, store, runtime):
    session_id, task_id = scope['session_id'], scope['owner_task_id']
    run = store.agents.get_run(session_id, args.run_id)
    before_count = len(run['prepared'])
    before = await save_draft(runtime, run, session_id, args.output_dir, 'before')
    report = {'source_database': str(source), 'source_opened_read_only': True, 'database': str(args.database),
        'run_id': run['id'], 'agent_id': run['agent_id'], 'task_id': task_id, 'original_input': run['message'],
        'implementation': implementation, 'initial_prepared_actions': before_count, 'before_observation_id': before,
        'stop_condition': ('terminal child handoff' if args.until_terminal else
            'first completed response batch containing a new native action, or terminal child handoff'),
        'artwork_acceptance': False, 'main_agent_started': False}
    (args.output_dir / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    started = time.time()
    try:
        store.update_task(task_id, 'running', '独立副本：继续原子 Agent 输入，检查纠错闭环。')
        await runtime.children.resume_agent(session_id, task_id, run['agent_id'], runtime.settings.load())
        previous = None
        while True:
            current = store.agents.get_run(session_id, run['id'])
            reviews = store.connection.execute('SELECT count(*) FROM draft_reviews WHERE run_id=?', (run['id'],)).fetchone()[0]
            progress = (current['status'], len(current['prepared']), reviews)
            if progress != previous:
                print(json.dumps({'status': progress[0], 'prepared_actions': progress[1], 'reviews': reviews,
                    'elapsed_ms': round((time.time() - started) * 1000, 1)}, ensure_ascii=False), flush=True)
                previous = progress
            worker = runtime.children.workers.get(run['agent_id'])
            if current['status'] in {'completed', 'failed'} or worker is None or worker.done():
                report['stopped_because'] = 'child_reached_terminal_state'
                break
            if not args.until_terminal and len(current['prepared']) > before_count and current['pending_response'] is None:
                report['stopped_because'] = 'new_native_action_batch_saved'
                break
            await asyncio.sleep(0.25)
    finally:
        await runtime.children.close_agent(session_id, run['agent_id'])
        current = store.agents.get_run(session_id, run['id'])
        report.update(elapsed_ms=round((time.time() - started) * 1000, 1),
            final_prepared_actions=len(current['prepared']), run_summary=store.agents.run_summary(current),
            after_observation_id=await save_draft(runtime, current, session_id, args.output_dir, 'after'))
        report['reviews'] = [json.loads(row['result']) for row in store.connection.execute(
            'SELECT result FROM draft_reviews WHERE run_id=? ORDER BY created_at', (run['id'],)).fetchall()]
        report['new_calls'] = [dict(row) for row in store.connection.execute(
            'SELECT call_id,name,arguments,output FROM agent_tool_calls WHERE caller_id=? AND created_at>=? '
            'ORDER BY created_at,rowid', (run['id'], started)).fetchall()]
        store.update_task(task_id, 'cancelled', '纠错诊断已停止；保留原有及新准备的动作。')
        (args.output_dir / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description='Resume one genuine stalled child on a read-only database snapshot.')
    parser.add_argument('--source-database', type=Path, required=True)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--until-terminal', action='store_true', help='Continue through real repairs and re-review until this input ends.')
    asyncio.run(evaluate(parser.parse_args()))


if __name__ == '__main__':
    main()
