import argparse
import hashlib
import html
import json
import runpy
import sqlite3
import sys
import time
from pathlib import Path


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


class ContextCapture:
    def __init__(self, database):
        from studio.llm import ModelGateway

        self.database = Path(database).resolve()
        if self.database.exists():
            raise ValueError('Choose a new context capture database')
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.database)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute('CREATE TABLE requests (id INTEGER PRIMARY KEY,started_at REAL NOT NULL,'
            'caller TEXT NOT NULL,owner TEXT NOT NULL,provider TEXT NOT NULL,model TEXT NOT NULL,'
            'request_sha256 TEXT NOT NULL,messages TEXT NOT NULL,tools TEXT NOT NULL,'
            'response TEXT,finished_at REAL)')
        self.frames = {}
        self.serialization_ms = 0
        self.gateway_code = ModelGateway.complete.__code__

    def __enter__(self):
        if sys.getprofile() is not None:
            raise ValueError('A context capture must not replace another profiler')
        sys.setprofile(self.observe)
        return self

    def __exit__(self, *error):
        sys.setprofile(None)
        self.connection.commit()
        self.connection.close()

    def observe(self, frame, event, value):
        if frame.f_code is not self.gateway_code:
            return
        if event == 'call' and 'options' not in frame.f_locals:
            started = time.perf_counter()
            local = frame.f_locals
            parent = frame.f_back
            owner = {key: parent.f_locals[key] for key in ('task_id', 'agent_id', 'round_number', 'child')
                if key in parent.f_locals} if parent else {}
            run = parent.f_locals.get('run') if parent else None
            if isinstance(run, dict):
                owner.update({key: run[key] for key in ('id', 'input_id', 'owner_task_id', 'agent_id') if key in run})
            messages, tools = encode(local['messages']), encode(local['tools'])
            digest = hashlib.sha256(encode({'messages': local['messages'], 'tools': local['tools']}).encode()).hexdigest()
            config = local['self'].config
            cursor = self.connection.execute('INSERT INTO requests '
                '(started_at,caller,owner,provider,model,request_sha256,messages,tools) VALUES(?,?,?,?,?,?,?,?)',
                (time.time(), parent.f_code.co_qualname if parent else '', encode(owner), config.provider,
                    config.model, digest, messages, tools))
            self.frames[id(frame)] = cursor.lastrowid
            self.connection.commit()
            self.serialization_ms += (time.perf_counter() - started) * 1000
        elif event == 'return' and getattr(value, 'choices', None) is not None:
            request_id = self.frames.pop(id(frame))
            self.connection.execute('UPDATE requests SET response=?,finished_at=? WHERE id=?',
                (encode(value.model_dump(mode='json', exclude_none=True)), time.time(), request_id))
            self.connection.commit()


def protocol_findings(messages):
    findings = []
    paired_outputs = set()
    for index, message in enumerate(messages):
        calls = message.get('tool_calls') if message.get('role') == 'assistant' else None
        if not calls:
            continue
        expected = [call['id'] for call in calls]
        returned = []
        for offset, following in enumerate(messages[index + 1:], index + 1):
            if following.get('role') != 'tool':
                break
            returned.append(following['tool_call_id'])
            paired_outputs.add(offset)
        if sorted(expected) != sorted(returned):
            findings.append({'message_index': index, 'expected_call_ids': expected, 'returned_call_ids': returned})
    findings.extend({'message_index': index, 'orphan_tool_call_id': message.get('tool_call_id')}
        for index, message in enumerate(messages) if message.get('role') == 'tool' and index not in paired_outputs)
    return findings


def inspect_messages(messages):
    result = []
    for index, message in enumerate(messages):
        content = message.get('content')
        blocks = content if isinstance(content, list) else [{'type': 'text', 'text': content}] if content else []
        keys, images, characters = [], [], 0
        for block in blocks:
            if block.get('type') == 'text' and isinstance(block.get('text'), str):
                characters += len(block['text'])
                try:
                    parsed = json.loads(block['text'])
                except json.JSONDecodeError:
                    parsed = None
                if isinstance(parsed, dict):
                    keys.extend(parsed)
            elif block.get('type') == 'image_url':
                images.append({'detail': block['image_url'].get('detail'),
                    'sha256': hashlib.sha256(block['image_url']['url'].encode()).hexdigest()})
        result.append({'index': index, 'role': message['role'], 'payload_keys': keys,
            'text_characters': characters, 'images': images, 'tool_call_id': message.get('tool_call_id'),
            'tool_names': [call['function']['name'] for call in message.get('tool_calls', [])],
            'message_sha256': hashlib.sha256(encode(message).encode()).hexdigest()})
    return result


def export_contexts(database, output):
    output = Path(output).resolve()
    output.mkdir()
    summary = []
    style = '<style>body{font:15px system-ui;margin:32px;max-width:1400px}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f4f5f7;padding:16px}img{max-width:100%;max-height:1000px}details{margin:12px 0}summary{cursor:pointer;font-weight:600}</style>'
    with sqlite3.connect(Path(database).resolve().as_uri() + '?mode=ro', uri=True) as connection:
        connection.row_factory = sqlite3.Row
        for row in connection.execute('SELECT * FROM requests ORDER BY id'):
            messages, tools = json.loads(row['messages']), json.loads(row['tools'])
            original = {'messages': messages, 'tools': tools}
            assert hashlib.sha256(encode(original).encode()).hexdigest() == row['request_sha256']
            response = json.loads(row['response']) if row['response'] else None
            owner = json.loads(row['owner'])
            filename = f"request-{row['id']:04d}"
            (output / f'{filename}.json').write_text(json.dumps(original, ensure_ascii=False, indent=2), encoding='utf-8')
            if response:
                (output / f'{filename}-response.json').write_text(json.dumps(response, ensure_ascii=False, indent=2), encoding='utf-8')
            rendered = [f'<meta charset="utf-8">{style}<h1>Request {row["id"]}</h1>',
                f'<pre>{html.escape(encode({"caller": row["caller"], "owner": owner, "request_sha256": row["request_sha256"]}))}</pre>']
            projection = []
            for index, message in enumerate(messages):
                attributes = {key: item for key, item in message.items() if key != 'content'}
                rendered.append(f'<details open><summary>{index} · {html.escape(message["role"])}</summary><pre>{html.escape(json.dumps(attributes, ensure_ascii=False, indent=2))}</pre>')
                projection.append(f'\n=== Message {index}: {encode(attributes)} ===\n')
                content = message.get('content')
                for block in content if isinstance(content, list) else [{'type': 'text', 'text': content}] if content else []:
                    if block.get('type') == 'image_url':
                        rendered.append(f'<img src="{html.escape(block["image_url"]["url"], quote=True)}">')
                        projection.append(f'[Actual attached image: {hashlib.sha256(block["image_url"]["url"].encode()).hexdigest()}]\n')
                    else:
                        text = block['text'] if block.get('type') == 'text' else json.dumps(block, ensure_ascii=False, indent=2)
                        rendered.append(f'<pre>{html.escape(text)}</pre>')
                        projection.append(text + '\n')
                rendered.append('</details>')
            rendered.append(f'<details><summary>Complete tool definitions</summary><pre>{html.escape(json.dumps(tools, ensure_ascii=False, indent=2))}</pre></details>')
            rendered.append(f'<h2>Actual response</h2><pre>{html.escape(json.dumps(response, ensure_ascii=False, indent=2))}</pre>')
            (output / f'{filename}.html').write_text('\n'.join(rendered), encoding='utf-8')
            (output / f'{filename}-text.txt').write_text(''.join(projection), encoding='utf-8')
            summary.append({'id': row['id'], 'caller': row['caller'], 'owner': owner,
                'request_sha256': row['request_sha256'], 'usage': response.get('usage') if response else None,
                'finish_reason': response['choices'][0].get('finish_reason') if response else None,
                'messages': inspect_messages(messages), 'tool_count': len(tools or []),
                'protocol_findings': protocol_findings(messages), 'response_recorded': response is not None})
    (output / 'manifest.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    links = [f'<li><a href="request-{item["id"]:04d}.html">{item["id"]}: {html.escape(item["caller"])}</a> · {len(item["messages"])} messages · {item["finish_reason"]}</li>' for item in summary]
    (output / 'index.html').write_text(f'<meta charset="utf-8">{style}<h1>Complete model contexts</h1><p>Actual ModelGateway arguments and responses. No headers or credentials are captured.</p><ol>' + ''.join(links) + '</ol>', encoding='utf-8')
    return summary


def main():
    parser = argparse.ArgumentParser(description='Record and inspect actual Agent model contexts.')
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--module')
    parser.add_argument('module_args', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.module:
        previous_args = sys.argv
        module_args = args.module_args[1:] if args.module_args[:1] == ['--'] else args.module_args
        capture = ContextCapture(args.database)
        try:
            with capture:
                sys.argv = [args.module, *module_args]
                runpy.run_module(args.module, run_name='__main__', alter_sys=True)
        finally:
            sys.argv = previous_args
            summary = export_contexts(args.database, args.output_dir)
            print(encode({'contexts': len(summary), 'incomplete_responses': sum(not item['response_recorded'] for item in summary),
                'context_serialization_ms': round(capture.serialization_ms, 3)}))
    else:
        if args.module_args:
            parser.error('Module arguments require --module')
        summary = export_contexts(args.database, args.output_dir)
        print(encode({'contexts': len(summary), 'incomplete_responses': sum(not item['response_recorded'] for item in summary)}))


if __name__ == '__main__':
    main()
