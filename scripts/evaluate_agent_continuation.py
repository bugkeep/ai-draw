import argparse
import asyncio
import hashlib
import json
import time
from pathlib import Path

from dotenv import load_dotenv

from studio.llm import close_model_clients
from studio.runtime import DrawingRuntime
from studio.settings import ModelSettings
from studio.store import StudioStore
from .evaluation_artifacts import capture_implementation


async def evaluate(args):
    if args.database.exists() or args.output_dir.exists():
        raise ValueError('Choose new isolated diagnostic paths')
    captured = json.loads(args.recording.read_text(encoding='utf-8'))
    if captured['response']['finish_reason'] != 'length' or captured['initial_document']['elements']:
        raise ValueError('This diagnostic needs an actual truncated first response on an empty starting draft')
    args.output_dir.mkdir(parents=True)
    implementation = capture_implementation(args.output_dir)
    store = StudioStore(args.database)
    runtime = DrawingRuntime(store, ModelSettings(store))
    config = runtime.settings.load()
    session = store.create_session('厂商截断后由主 Agent 继续')
    request = '继续当前子 Agent 尚未完成的绘图输入，保留已准备的动作与草稿，完成剩余工作。'
    task = store.create_task(session['id'])
    store.add_message(session['id'], 'user', request, task['id'])
    store.update_task(task['id'], 'running')
    agent = store.agents.create_agent(session['id'], task['id'], 'recorded-provider-length',
        captured['assignment'], 'Landscape', [])
    run = runtime.children._initialize(session['id'], store.agents.next_run(session['id'], agent['agent_id'], task['id']))
    store.log_tool(task['id'], agent['agent_id'], 0, 'llm_response', captured['response'])
    store.agents.save_response(run, captured['response']['message'])
    report = {'diagnostic': 'recorded provider truncation plus live main/child continuation',
        'seed_provenance': captured['provenance'], 'seed_is_recorded_response': True,
        'recording_sha256': hashlib.sha256(args.recording.read_bytes()).hexdigest(),
        'diagnostic_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'implementation_sha256': implementation, 'model': config.model, 'provider': config.provider,
        'model_parameters_unchanged': True, 'database': str(args.database), 'session_id': session['id'],
        'task_id': task['id'], 'agent_id': agent['agent_id'], 'input_id': run['input_id'], 'run_id': run['id'],
        'artwork_acceptance': False, 'stop_condition': 'new complete native-action batch in the SAME input after main acknowledgment, or terminal failure'}
    report_path = args.output_dir / 'report.json'
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    started = time.time()
    try:
        runtime.children._start(session['id'], task['id'], agent['agent_id'], config)
        observed = await runtime.children.wait_agent(session['id'], [agent['agent_id']])
        event, = observed['continuations']
        interrupted = store.agents.get_run(session['id'], run['id'])
        before_count = len(interrupted['prepared'])
        report.update(continuation_id=event['continuation_id'], initial_prepared_actions=before_count)
        print(json.dumps({'phase': 'awaiting_main_continuation', **event}, ensure_ascii=False), flush=True)
        worker = asyncio.create_task(runtime._run(task['id'], session['id'], session['document']['id'], request))
        runtime.running[task['id']] = worker
        previous = None
        while True:
            current = store.agents.get_run(session['id'], run['id'])
            receipt = store.connection.execute('SELECT status,continued_at FROM agent_continuations WHERE id=?',
                (event['continuation_id'],)).fetchone()
            progress = (current['status'], len(current['prepared']), receipt['status'])
            if progress != previous:
                print(json.dumps({'phase': 'progress', 'status': progress[0], 'prepared_actions': progress[1],
                    'continuation_status': progress[2], 'elapsed_ms': round((time.time() - started) * 1000, 1)},
                    ensure_ascii=False), flush=True)
                previous = progress
            pending = store.agents.pending_continuations(session['id'], agent['agent_id'])
            if (receipt['status'] == 'continued' and len(current['prepared']) > before_count
                    and current['pending_response'] is None and not pending):
                report['stopped_because'] = 'live_action_batch_saved_after_main_continuation'
                break
            if current['status'] in {'completed', 'failed'} or worker.done():
                report['stopped_because'] = 'execution_ended_before_new_action_batch'
                break
            await asyncio.sleep(0.2)
    finally:
        parent = runtime.running.get(task['id'])
        if parent:
            runtime.cancel(task['id'])
            await asyncio.gather(parent, return_exceptions=True)
        await runtime.children.close_agent(session['id'], agent['agent_id'])
        current = store.agents.get_run(session['id'], run['id'])
        logs = store.tool_logs(task['id'])
        confirmations = [json.loads(row['payload']['output']) for row in logs
            if row['kind'] == 'function_call_output' and row['agent_id'] == 'main'
            and json.loads(row['payload']['output']).get('continuation_id') == report.get('continuation_id')]
        actual_child_requests = [row for row in logs if row['kind'] == 'model_context' and row['agent_id'] == agent['agent_id']]
        original_errors = {call['id'] for call in captured['response']['message'].get('tool_calls', [])
            if any(row['kind'] == 'function_call_output' and row['payload']['call_id'] == call['id']
                and json.loads(row['payload']['output']).get('ok') is False for row in logs)}
        delivered_errors = {call_id for row in actual_child_requests for call_id in row['payload']['tool_error_ids']}
        document = {**current['document'], 'elements': current['draft'], 'selection': current['selection']}
        image = await runtime.vision.observe(task['id'], agent['agent_id'], f"diagnostic:{run['id']}", document)
        (args.output_dir / 'continued-draft.png').write_bytes(runtime.vision.image(session['id'], image['observation_id']))
        report.update(elapsed_ms=round((time.time() - started) * 1000, 1),
            final_prepared_actions=len(current['prepared']), main_confirmation_outputs=confirmations,
            live_child_request_count=len(actual_child_requests),
            original_tool_error_ids=sorted(original_errors), delivered_tool_error_ids=sorted(delivered_errors),
            original_errors_delivered=bool(original_errors) and original_errors <= delivered_errors,
            current_draft_images_delivered=any(row['payload']['image_observation_ids'] for row in actual_child_requests),
            shared_element_count=len(store.get_document(session['document']['id'])['elements']),
            same_input_and_run=bool(confirmations) and all(
                item['input_id'] == run['input_id'] and item['run_id'] == run['id'] for item in confirmations),
            input_count=store.connection.execute('SELECT count(*) FROM agent_inputs WHERE agent_id=?', (agent['agent_id'],)).fetchone()[0],
            whole_artwork_completed=False)
        report['protocol_passed'] = bool(report.get('stopped_because') == 'live_action_batch_saved_after_main_continuation'
            and report['same_input_and_run'] and report['live_child_request_count'] and report['input_count'] == 1
            and report['original_errors_delivered'] and report['current_draft_images_delivered'])
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        await runtime.close()
        await close_model_clients()
        store.close()
    return report


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description='Replay a recorded length response, then let the actual main Agent continue its child.')
    parser.add_argument('--recording', type=Path, default=Path('tests/fixtures/recorded_truncated_child_response.json'))
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    report = asyncio.run(evaluate(parser.parse_args()))
    print(json.dumps({key: report[key] for key in ('protocol_passed', 'same_input_and_run', 'input_count',
        'live_child_request_count', 'initial_prepared_actions', 'final_prepared_actions', 'elapsed_ms', 'artwork_acceptance')},
        ensure_ascii=False), flush=True)
    if not report['protocol_passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
