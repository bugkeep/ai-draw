"""Measure compact scene events against an existing full-snapshot event archive, without model calls."""

import argparse
import hashlib
import json
import sqlite3
import subprocess
from pathlib import Path

from studio.scene_patch import scene_patch


def evaluate(source: Path, document_id: str | None, output: Path):
    root = Path(__file__).resolve().parent.parent
    connection = sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True)
    connection.row_factory = sqlite3.Row
    try:
        if not document_id:
            row = connection.execute('SELECT document_id FROM events WHERE type=\'scene\' '
                'GROUP BY document_id ORDER BY SUM(length(payload)) DESC LIMIT 1').fetchone()
            if not row:
                raise ValueError('Choose an archive containing full scene events')
            document_id = row[0]
        document = None
        initial = None
        events = []
        source_bytes = 0
        patch_bytes = 0
        for row in connection.execute('SELECT seq,payload FROM events WHERE document_id=? AND type=\'scene\' '
                                      'ORDER BY seq', (document_id,)):
            payload = json.loads(row['payload'])
            current = payload['document']
            source_bytes += len(row['payload'].encode('utf-8'))
            if document is None:
                initial = {**current, 'version': current['version'] - 1, 'elements': [], 'selection': []}
                document = initial
            if current['version'] <= document['version']:
                continue
            if current['version'] != document['version'] + 1:
                raise ValueError('The archive has a gap in scene versions')
            compact = {**{key: value for key, value in payload.items() if key != 'document'},
                       'patch': scene_patch(document, current)}
            events.append({'seq': row['seq'], 'type': 'scene_patch', 'payload': compact})
            patch_bytes += len(json.dumps(compact, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
            document = current
        if document is None:
            raise ValueError('This document has no saved full scene events')
        final = connection.execute('SELECT elements,selection FROM documents WHERE id=?', (document_id,)).fetchone()
        if final is None:
            raise ValueError('The archived document is missing')
        expected = {**document, 'elements': json.loads(final['elements']), 'selection': json.loads(final['selection'])}
    finally:
        connection.close()
    output.parent.mkdir(parents=True, exist_ok=True)
    fixture = output.with_suffix('.replay.json')
    fixture.write_text(json.dumps({'initial': initial, 'events': events, 'expected': expected}, ensure_ascii=False), encoding='utf-8')
    process = subprocess.run(['node', str(root / 'frontend-app/scripts/replay_scene_events.js'), str(fixture.resolve())],
                             cwd=root, capture_output=True, text=True, encoding='utf-8', check=True)
    checks = json.loads(process.stdout)
    report = {'source_database': str(source), 'document_id': document_id, 'model_requests': 0,
              'measurement': 'replay_of_actual_saved_scene_events', 'scene_patch_count': len(events),
              'final_native_objects': sum(not element.get('isDeleted') for element in expected['elements']),
              'source_scene_payload_bytes': source_bytes, 'scene_patch_payload_bytes': patch_bytes,
              'payload_reduction_percent': 100 * (1 - patch_bytes / source_bytes), 'checks': checks,
              'implementation_sha256': {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
                  for path in (root / 'studio/scene_patch.py', root / 'frontend-app/src/scenePatch.js')},
              'replay_fixture': str(fixture)}
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-database', type=Path, required=True)
    parser.add_argument('--document-id')
    parser.add_argument('--output', type=Path, default=Path('runs/scene-events-evaluation.json'))
    args = parser.parse_args()
    print(json.dumps(evaluate(args.source_database, args.document_id, args.output), ensure_ascii=True, indent=2))


if __name__ == '__main__':
    main()
