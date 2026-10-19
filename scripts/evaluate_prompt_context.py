"""Measure real read-only model requests with old/new role text and current tool constraints."""

import argparse
import ast
import asyncio
import hashlib
import json
import subprocess
import time
from pathlib import Path

from dotenv import load_dotenv

from studio.agent_tools import CHILD_INPUTS, CHILD_TOOLS, MAIN_INPUTS, MAIN_TOOLS, validate_tool
from studio.actions import create_native_element
from studio.agents import prepare_drawing_tool
from studio.llm import ModelGateway, close_model_clients
from studio.prompts import CHILD_SYSTEM, MAIN_SYSTEM
from studio.settings import ModelSettings
from studio.store import StudioStore


def baseline_policies(revision, root):
    found = {}
    for name in ('studio/agents.py', 'studio/prompts.py'):
        result = subprocess.run(['git', 'show', f'{revision}:{name}'], cwd=root, text=True,
                                encoding='utf-8', capture_output=True, check=False)
        if result.returncode:
            continue
        for node in ast.parse(result.stdout).body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id in {'MAIN_SYSTEM', 'CHILD_SYSTEM'}:
                        found[target.id] = ast.literal_eval(node.value)
    if set(found) != {'MAIN_SYSTEM', 'CHILD_SYSTEM'}:
        raise ValueError('The requested baseline revision has no readable literal role policies')
    return found


async def evaluate(args):
    if args.database.exists():
        raise ValueError('Use a new independent measurement database')
    root = Path(__file__).resolve().parent.parent
    baseline = baseline_policies(args.baseline_revision, root)
    store = StudioStore(args.database)
    config = ModelSettings(store).load()
    gateway = ModelGateway(config)
    session = store.create_session('只读上下文测量')
    task = store.create_task(session['id'])
    store.save_snapshot(session['document']['id'], 0, [create_native_element({
        'type': 'rectangle', 'name': 'figure silhouette', 'x': 10, 'y': 20, 'width': 80, 'height': 120})], [])
    before = store.get_document(session['document']['id'])
    before_history = store.get_history(before['id'], 'undo')
    measurements = []
    try:
        for role, policy, tools, inputs in [('main', MAIN_SYSTEM, MAIN_TOOLS, MAIN_INPUTS),
                                           ('child', CHILD_SYSTEM, CHILD_TOOLS, CHILD_INPUTS)]:
            old_tools = [{**tool, 'function': {**tool['function'],
                'parameters': inputs[tool['function']['name']].model_json_schema()}} for tool in tools]
            for version, text, definitions in [('before', baseline[f'{role.upper()}_SYSTEM'], old_tools),
                                               ('after', policy, tools)]:
                messages = [{'role': 'system', 'content': text}, {'role': 'user', 'content':
                    'Inspect objects whose name contains figure, using one inspect_scene page with limit 6. '
                    'This is only an object lookup; preserve the canvas.'}]
                started = time.perf_counter()
                response = await gateway.complete(messages, definitions)
                latency = round((time.perf_counter() - started) * 1000, 1)
                message = response.choices[0].message.model_dump(exclude_none=True)
                usage = response.usage.model_dump() if response.usage else {}
                calls = message.get('tool_calls') or []
                valid = len(calls) == 1
                outputs = []
                for call in calls:
                    try:
                        arguments = validate_tool(call['function']['name'], json.loads(call['function']['arguments']), child=role == 'child')
                        valid = valid and call['function']['name'] == 'inspect_scene' and arguments.get('query') == 'figure' and arguments.get('limit') == 6
                        if call['function']['name'] == 'inspect_scene':
                            _, _, actions, output = prepare_drawing_tool('inspect_scene', arguments, before,
                                before['elements'], before['selection'])
                            valid = valid and not actions and len(output['scene']['elements']) == 1
                            outputs.append(output)
                    except (KeyError, ValueError, TypeError):
                        valid = False
                record = {'role': role, 'version': version, 'system_characters': len(text),
                    'tool_characters': len(json.dumps(definitions, ensure_ascii=False, separators=(',', ':'))),
                    'tool_schema_sha256': hashlib.sha256(json.dumps(definitions, sort_keys=True).encode()).hexdigest(),
                    'user_request': messages[-1]['content'],
                    'policy_sha256': hashlib.sha256(text.encode()).hexdigest(), 'usage': usage,
                    'latency_ms': latency, 'actual_read_only_tool_request_valid': valid, 'message': message,
                    'executed_read_only_outputs': outputs}
                store.log_tool(task['id'], f'prompt-{role}-{version}', 0, 'llm_response', record)
                measurements.append(record)
        checks = {'actual_read_only_requests_valid': all(item['actual_read_only_tool_request_valid'] for item in measurements),
            'canvas_and_history_untouched': store.get_document(before['id']) == before and store.get_history(before['id'], 'undo') == before_history}
        for role in ('main', 'child'):
            old, new = [item for item in measurements if item['role'] == role]
            old_tokens, new_tokens = old['usage'].get('prompt_tokens'), new['usage'].get('prompt_tokens')
            checks[f'{role}_request_tokens_reduced'] = old_tokens is not None and new_tokens is not None and new_tokens < old_tokens
        store.update_task(task['id'], 'completed', 'Read-only prefix requests recorded; no drawing tools executed.')
        result = {'provider': config.provider, 'model': config.model, 'baseline_revision': args.baseline_revision,
            'measurement': 'role-text and schema-annotation ablation with identical current tool constraints; real read-only calls, no drawing actions executed',
            'not_an_end_to_end_drawing_latency_measurement': True, 'checks': checks,
            'passed': all(checks.values()), 'requests': measurements}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'passed': result['passed'], 'checks': checks, 'requests': [
            {key: item[key] for key in ('role', 'version', 'system_characters', 'tool_characters', 'usage', 'latency_ms')}
            for item in measurements]}, ensure_ascii=True), flush=True)
        return result['passed']
    finally:
        await close_model_clients()
        store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline-revision', required=True)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    load_dotenv(Path(__file__).resolve().parent.parent / '.env', override=False)
    if not asyncio.run(evaluate(args)):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
