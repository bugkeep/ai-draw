import gzip
import hashlib
import html
import json
from pathlib import Path

from scripts.inspect_agent_contexts import ContextCapture, export_contexts


def test_recorded_model_request_exports_complete_messages_images_tools_and_actual_response(tmp_path):
    fixture = Path(__file__).parent / 'fixtures' / 'recorded_context_capture.json.gz'
    with gzip.open(fixture, 'rt', encoding='utf-8') as source:
        recorded = json.load(source)
    row = recorded['request']
    capture = ContextCapture(tmp_path / 'capture.sqlite3')
    columns = list(row)
    capture.connection.execute(f"INSERT INTO requests ({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",
        [row[key] for key in columns])
    capture.connection.commit()
    capture.connection.close()
    before = hashlib.sha256(capture.database.read_bytes()).hexdigest()
    output = tmp_path / 'complete-context'
    manifest = export_contexts(capture.database, output)
    assert hashlib.sha256(capture.database.read_bytes()).hexdigest() == before
    expected = {'messages': json.loads(row['messages']), 'tools': json.loads(row['tools'])}
    stem = f"request-{row['id']:04d}"
    assert json.loads((output / f'{stem}.json').read_text(encoding='utf-8')) == expected
    assert json.loads((output / f'{stem}-response.json').read_text(encoding='utf-8')) == json.loads(row['response'])
    rendered = (output / f'{stem}.html').read_text(encoding='utf-8')
    images = []
    for message in expected['messages']:
        content = message.get('content')
        blocks = content if isinstance(content, list) else [{'type': 'text', 'text': content}] if content else []
        for block in blocks:
            if block['type'] == 'image_url':
                images.append(block['image_url']['url'])
                assert html.escape(block['image_url']['url'], quote=True) in rendered
            elif block['type'] == 'text':
                assert html.escape(block['text']) in rendered
    assert images
    assert rendered.count('<img ') == len(images)
    assert manifest[0]['request_sha256'] == row['request_sha256']
    assert [item['role'] for item in manifest[0]['messages']] == [item['role'] for item in expected['messages']]
    assert manifest[0]['protocol_findings'] == []
    assert manifest[0]['tool_count'] == len(expected['tools'])
