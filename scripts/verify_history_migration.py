import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

from studio.store import StudioStore


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def digest_rows(rows):
    checksum = hashlib.sha256()
    count = 0
    for row in rows:
        checksum.update(encode([{'blob_sha256': hashlib.sha256(value).hexdigest()} if isinstance(value, bytes)
                                else value for value in row]))
        checksum.update(b'\n')
        count += 1
    return {'count': count, 'sha256': checksum.hexdigest()}


def snapshot(path):
    with sqlite3.connect(f'{path.resolve().as_uri()}?mode=ro', uri=True) as connection:
        connection.execute('BEGIN')
        tables = [row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        content = {}
        for table in tables:
            if table in {'documents', 'document_history'}:
                continue
            quoted = '"' + table.replace('"', '""') + '"'
            content[table] = digest_rows(connection.execute(f'SELECT * FROM {quoted} ORDER BY rowid'))
        columns = [row[1] for row in connection.execute('PRAGMA table_info(documents)')
                   if row[1] not in {'undo_history', 'redo_history'}]
        selected = ','.join('"' + column.replace('"', '""') + '"' for column in columns)
        content['documents'] = digest_rows(connection.execute(f'SELECT {selected} FROM documents ORDER BY id'))
        histories = {}
        for document_id, legacy_undo, legacy_redo in connection.execute(
                'SELECT id,undo_history,redo_history FROM documents ORDER BY id'):
            histories[document_id] = {}
            for direction, legacy in (('undo', legacy_undo), ('redo', legacy_redo)):
                rows = list(connection.execute('SELECT snapshot FROM document_history WHERE document_id=? '
                    'AND direction=? ORDER BY id', (document_id, direction))) if 'document_history' in tables else []
                if rows and json.loads(legacy):
                    raise ValueError('History contains both storage formats')
                states = (json.loads(row[0]) for row in rows) if rows else iter(json.loads(legacy))
                histories[document_id][direction] = digest_rows((encode(state).decode('utf-8'),) for state in states)
        return {'tables': content, 'histories': histories,
                'quick_check': connection.execute('PRAGMA quick_check').fetchone()[0],
                'foreign_key_errors': connection.execute('PRAGMA foreign_key_check').fetchall()}


def compare(before, after):
    changes = [table for table, value in before['tables'].items() if after['tables'].get(table) != value]
    checks = {'document_content_and_original_tables_preserved': not changes,
              'all_history_snapshots_and_order_preserved': before['histories'] == after['histories'],
              'sqlite_integrity_ok': before['quick_check'] == after['quick_check'] == 'ok',
              'foreign_keys_valid': not before['foreign_key_errors'] and not after['foreign_key_errors']}
    return {'checks': checks, 'changed_tables': changes, 'session_count': after['tables']['sessions']['count'],
            'document_count': len(after['histories']), 'passed': all(checks.values())}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-database', type=Path, required=True)
    destinations = parser.add_mutually_exclusive_group(required=True)
    destinations.add_argument('--database', type=Path)
    destinations.add_argument('--compare-database', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    before = snapshot(args.source_database)
    if args.database:
        if args.database.exists():
            raise ValueError('Use a new independent migration database')
        args.database.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(f'{args.source_database.resolve().as_uri()}?mode=ro', uri=True) as source:
            with sqlite3.connect(args.database) as destination:
                source.backup(destination)
        migrated = StudioStore(args.database)
        migrated.close()
    destination = args.database or args.compare_database
    after = snapshot(destination)
    result = {'source_database': str(args.source_database), 'database': str(destination),
              'store_source_sha256': hashlib.sha256((Path(__file__).resolve().parent.parent / 'studio/store.py').read_bytes()).hexdigest(),
              **compare(before, after)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False), flush=True)
    if not result['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
