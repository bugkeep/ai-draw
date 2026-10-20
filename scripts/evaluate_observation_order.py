import argparse
import asyncio
import copy
import hashlib
import json
import shutil
import sqlite3
import statistics
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv

from studio.agent_tools import validate_tool
from studio.agents import DrawingAgents, prepare_drawing_tool
from studio.child_harness import observation_context
from studio.llm import close_model_clients
from studio.runtime import DrawingRuntime
from studio.settings import ModelSettings
from studio.store import StudioStore
from studio.vision import scene_digest
from .evaluation_artifacts import capture_implementation


def message_counts(messages):
    return Counter(json.dumps(message, ensure_ascii=False, sort_keys=True) for message in messages)


def execute_lookup(runtime, run, message, query):
    outputs = []
    for call in message.get('tool_calls') or []:
        name, raw = call['function']['name'], call['function']['arguments']
        if name != 'inspect_scene':
            raise ValueError(f'Read-only measurement received {name}; no drawing action was executed')
        arguments = validate_tool(name, json.loads(raw), child=True)
        if arguments.get('query') != query or arguments.get('limit') != 6 or arguments.get('offset', 0) != 0:
            raise ValueError('The actual model did not perform the requested lookup')
        cached = runtime.store.agents.begin_call(run['owner_task_id'], run['id'], call['id'], name, raw)
        assert cached is None
        draft, selected, actions, output = prepare_drawing_tool(name, arguments, run['document'], run['draft'], run['selection'])
        assert not actions and draft == run['draft'] and selected == run['selection']
        runtime.store.agents.finish_call(run['id'], call['id'], output, run)
        context_output = runtime.memory.for_context(run['owner_task_id'], run['agent_id'], run['id'], call['id'],
            name, raw, output, f"{run['id']}:{len(run['prepared'])}")
        outputs.append({'role': 'tool', 'tool_call_id': call['id'], 'content': json.dumps(context_output, ensure_ascii=False)})
    if not outputs:
        raise ValueError('The actual model returned without the requested read-only call')
    return outputs


async def evaluate(args):
    if args.database.exists() or args.output_dir.exists() or len(args.queries) < 2:
        raise ValueError('Choose new independent paths and at least two lookup queries')
    with sqlite3.connect(args.source_database.resolve().as_uri() + '?mode=ro', uri=True) as source:
        source.row_factory = sqlite3.Row
        row = source.execute('SELECT draft,selection FROM agent_runs WHERE id=?', (args.run_id,)).fetchone()
        if row is None:
            raise ValueError('Select an actual recorded native draft')
        elements, selection = json.loads(row['draft']), json.loads(row['selection'])
        viewport = source.execute('SELECT region FROM canvas_viewports WHERE caller_id=?', (args.run_id,)).fetchone()
        region = json.loads(viewport['region']) if viewport else None
    args.output_dir.mkdir(parents=True)
    args.database.parent.mkdir(parents=True, exist_ok=True)
    implementation = capture_implementation(args.output_dir)
    shutil.copyfile(__file__, args.output_dir / 'diagnostic.py')
    store = StudioStore(args.database)
    runtime = DrawingRuntime(store, ModelSettings(store))
    settings = runtime.settings.load()
    models = DrawingAgents(store, settings)
    session = store.create_session('当前图片消息顺序：真实只读模型对照')
    store.save_snapshot(session['document']['id'], 0, elements, selection)
    task = store.create_task(session['id'])
    actor = store.agents.create_agent(session['id'], task['id'], 'observation-order-read-only',
        '进行只读对象检查。每轮会给出具体名称查询；使用 inspect_scene 返回一个页面，保留全部画布对象。', 'Read-only', [])
    run = runtime.children._initialize(session['id'], store.agents.next_run(session['id'], actor['agent_id'], task['id']))
    agent = store.agents.get_agent(session['id'], actor['agent_id'])
    document = {**run['document'], 'elements': run['draft'], 'selection': run['selection']}
    before = store.get_document(document['id'])
    history_before = store.get_history(document['id'], 'undo')
    report = {'source_database': str(args.source_database), 'source_run_id': args.run_id,
        'source_opened_read_only': True, 'implementation': implementation,
        'diagnostic_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'measurement': 'identical message contents and tools, alternating ABBA order, actual read-only model requests',
        'main_agent_started': False, 'artwork_acceptance': False, 'end_to_end_drawing_latency_measurement': False,
        'scene_hash': scene_digest(document), 'initial_order': args.first,
        'model_configuration_sha256': hashlib.sha256(json.dumps(settings.model_dump(mode='json', exclude={'api_key'}),
            sort_keys=True).encode()).hexdigest(), 'requests': []}
    transcript = []
    try:
        if region:
            await runtime.vision.observe(task['id'], run['agent_id'], run['id'], document, region, remember_region=True)
        pictures, observations = await runtime.vision.context(task['id'], run['agent_id'], run['id'], document)
        report['observation_ids'] = observations
        for pair_index, query in enumerate(args.queries):
            run['conversation'] = transcript
            prepared = runtime.memory.prepare_messages(task['id'], run['agent_id'], run['id'],
                f"{run['id']}:{len(run['prepared'])}", document, models.child_messages(run, agent))
            request = {'role': 'user', 'content': f'当前只读检查：调用 inspect_scene，query={query}，limit=6，offset=0。保留画布。'}
            contexts = {'baseline': [*prepared, *pictures, request],
                'candidate': [*observation_context(prepared, [], pictures), request]}
            assert message_counts(contexts['baseline']) == message_counts(contexts['candidate'])
            baseline_first = (pair_index % 2 == 0) == (args.first == 'baseline')
            order = ('baseline', 'candidate') if baseline_first else ('candidate', 'baseline')
            results = {}
            for position, version in enumerate(order):
                messages = copy.deepcopy(contexts[version])
                message = await models.respond(task['id'], run['agent_id'], pair_index * 2 + position + 1,
                    messages, child=True, observation_ids=observations)
                runtime.vision.delivered(task['id'], run['id'], observations)
                runtime.memory.delivered(run['id'], messages)
                outputs = execute_lookup(runtime, run, message, query)
                response = json.loads(store.connection.execute("SELECT payload FROM tool_logs WHERE task_id=? AND agent_id=? "
                    "AND kind='llm_response' ORDER BY id DESC LIMIT 1", (task['id'], run['agent_id'])).fetchone()['payload'])
                record = {'pair_index': pair_index, 'position': position, 'version': version, 'query': query,
                    'latency_ms': response['latency_ms'], 'usage': response['usage'], 'message': message,
                    'exact_same_input_messages': True, 'executed_read_only_outputs': outputs}
                report['requests'].append(record)
                results[version] = [message, *outputs]
                (args.output_dir / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
                print(json.dumps({key: record[key] for key in ('pair_index', 'position', 'version', 'query', 'latency_ms', 'usage')}), flush=True)
            transcript.extend(results[order[0]])
        report['canvas_and_history_unchanged'] = (before == store.get_document(document['id'])
            and history_before == store.get_history(document['id'], 'undo') and not run['prepared'])
        assert report['canvas_and_history_unchanged']
        report['warm_summary'] = {version: {'median_latency_ms': statistics.median(record['latency_ms']
            for record in report['requests'] if record['version'] == version and record['pair_index'] > 0)}
            for version in ('baseline', 'candidate')}
        report['protocol_passed'] = True
        return report
    finally:
        (args.output_dir / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        await runtime.close()
        await close_model_clients()
        store.close()


def main():
    parser = argparse.ArgumentParser(description='Compare child observation ordering with real read-only calls on a recorded native draft.')
    parser.add_argument('--source-database', type=Path, required=True)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--queries', nargs='+', required=True)
    parser.add_argument('--first', choices=['baseline', 'candidate'], default='baseline')
    parser.add_argument('--env-file', type=Path, default=Path(__file__).resolve().parent.parent / '.env')
    args = parser.parse_args()
    load_dotenv(args.env_file)
    asyncio.run(evaluate(args))


if __name__ == '__main__':
    main()
