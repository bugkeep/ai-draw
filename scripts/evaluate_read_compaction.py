"""Compare real next decisions from an archived child context; never execute tools.

This is a diagnostic of input size/next decisions, not artwork acceptance or a
claim that a stalled task recovered. The source database is opened read-only.
"""

import argparse
import asyncio
import base64
import hashlib
import json
import sqlite3
import time
from pathlib import Path

from dotenv import load_dotenv

from studio.agent_tools import CHILD_TOOLS
from studio.agents import DrawingAgents
from studio.llm import ModelGateway, close_model_clients
from studio.memory import AgentMemory
from studio.reference_images import ReferenceImages
from studio.settings import ModelSettings
from studio.store import StudioStore
from studio.vision import CanvasVision
from .evaluation_artifacts import capture_implementation


def save_input(messages, directory):
    directory.mkdir(parents=True)
    saved = []
    for message in messages:
        if not isinstance(message.get('content'), list):
            saved.append(message)
            continue
        parts = []
        for part in message['content']:
            if part.get('type') != 'image_url':
                parts.append(part)
                continue
            header, data = part['image_url']['url'].split(',', 1)
            if header != 'data:image/png;base64':
                raise ValueError('Diagnostics only archive locally supplied PNG images')
            png = base64.b64decode(data, validate=True)
            name = hashlib.sha256(png).hexdigest() + '.png'
            (directory / name).write_bytes(png)
            parts.append({'type': 'image_file', 'path': name,
                          'detail': part['image_url'].get('detail', 'auto')})
        saved.append({**message, 'content': parts})
    (directory / 'messages.json').write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding='utf-8')
    return len(json.dumps(saved, ensure_ascii=False))


async def evaluate(args):
    if args.database.exists() or args.output_dir.exists():
        raise ValueError('Use new isolated output paths')
    source = args.source_database.resolve()
    with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True) as archived:
        archived.row_factory = sqlite3.Row
        row = archived.execute('SELECT r.owner_task_id,t.session_id,t.status FROM agent_runs r '
            'JOIN tasks t ON t.id=r.owner_task_id WHERE r.id=?', (args.run_id,)).fetchone()
        if row is None or row['status'] not in {'cancelled', 'completed', 'failed', 'partial'}:
            raise ValueError('Choose a terminal archived task; do not diagnose a running task this way')
        args.database.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(args.database) as copied:
            archived.backup(copied)
        scope = dict(row)
    args.output_dir.mkdir(parents=True)
    implementation = capture_implementation(args.output_dir)
    store = StudioStore(args.database)
    references = ReferenceImages(store)
    try:
        memory, vision = AgentMemory(store), CanvasVision(store)
        config = ModelSettings(store).load()
        models = DrawingAgents(store, config)
        run = store.agents.get_run(scope['session_id'], args.run_id)
        agent = store.agents.get_agent(scope['session_id'], run['agent_id'])
        document = {**run['document'], 'elements': run['draft'], 'selection': run['selection']}
        originals = models.child_messages(run, agent)
        folded = memory.fold_reads(run['id'], originals)
        prepared = memory.prepare_messages(run['owner_task_id'], run['agent_id'], run['id'],
            f"{run['id']}:{len(run['prepared'])}", document, originals)
        tail = prepared[len(folded):]  # identical working notes and unread errors
        reference_messages, reference_ids = await references.prepare_context(run['owner_task_id'], run['id'])
        pictures, observation_ids = await vision.context(run['owner_task_id'], run['agent_id'], run['id'], document)
        variants = {'original': [*originals, *tail, *reference_messages, *pictures],
                    'folded': [*folded, *tail, *reference_messages, *pictures]}
        report = {'source_database': str(source), 'source_opened_read_only': True,
            'database': str(args.database), 'run_id': run['id'], 'task_id': run['owner_task_id'],
            'task_status': scope['status'], 'prepared_action_count': len(run['prepared']),
            'provider': config.provider, 'model': config.model, 'implementation': implementation,
            'reference_image_view_ids': reference_ids, 'observation_ids': observation_ids,
            'tools_executed': False, 'artwork_acceptance': False, 'variants': {}}
        gateway = ModelGateway(config)
        for name, messages in variants.items():
            characters = save_input(messages, args.output_dir / name)
            started = time.perf_counter()
            response = await gateway.complete(messages, CHILD_TOOLS)
            result = {'archived_input_characters': characters, 'message_count': len(messages),
                'tool_outputs': [message['tool_call_id'] for message in messages if message.get('role') == 'tool'],
                'latency_ms': round((time.perf_counter() - started) * 1000, 1),
                'response': response.choices[0].message.model_dump(exclude_none=True),
                'usage': response.usage.model_dump() if response.usage else None}
            report['variants'][name] = result
            (args.output_dir / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps({'variant': name, 'characters': characters, 'latency_ms': result['latency_ms'],
                'next_tools': [call['function']['name'] for call in result['response'].get('tool_calls', [])]},
                ensure_ascii=False), flush=True)
    finally:
        await close_model_clients()
        store.close()


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-database', type=Path, required=True)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    asyncio.run(evaluate(parser.parse_args()))


if __name__ == '__main__':
    main()
