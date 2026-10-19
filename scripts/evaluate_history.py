import argparse
import cProfile
import hashlib
import json
import pstats
import sqlite3
import statistics
import time
from pathlib import Path

from studio.store import StudioStore


def measure(operation, count):
    durations, values = [], []
    for _ in range(count):
        started = time.perf_counter()
        values.append(operation())
        durations.append(round((time.perf_counter() - started) * 1000, 2))
    return {'samples_ms': durations, 'median_ms': round(statistics.median(durations), 2)}, values


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-database', type=Path, required=True)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--session-id')
    parser.add_argument('--steps', type=int, default=6)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.database.exists() or args.steps < 1:
        raise ValueError('Use a new independent database and a positive sample count')
    args.database.parent.mkdir(parents=True, exist_ok=True)
    source_hash = hashlib.sha256((Path(__file__).resolve().parent.parent / 'studio/store.py').read_bytes()).hexdigest()
    with sqlite3.connect(f'{args.source_database.resolve().as_uri()}?mode=ro', uri=True) as source:
        with sqlite3.connect(args.database) as destination:
            source.backup(destination)
    store = StudioStore(args.database)
    try:
        document_id = store.session_document_id(args.session_id) if args.session_id else store.connection.execute(
            'SELECT id FROM documents ORDER BY length(undo_history) DESC LIMIT 1').fetchone()['id']
        before = store.get_document(document_id)
        target = next(item['id'] for item in before['elements'] if not item.get('isDeleted'))
        row = store.connection.execute('SELECT undo_history,length(CAST(elements AS BLOB)) scene_bytes,'
            'length(CAST(undo_history AS BLOB)) history_bytes FROM documents WHERE id=?', (document_id,)).fetchone()
        prior_undo = json.loads(row['undo_history'])
        unrelated = list(store.connection.execute('SELECT * FROM documents WHERE id<>? ORDER BY id', (document_id,)))
        profile = cProfile.Profile()
        profile.enable()
        moves, values = measure(lambda: store.commit_action(document_id, {
            'kind': 'move_elements', 'args': {'ids': [target], 'dx': 1, 'dy': 0}}, {})['document'], args.steps)
        profile.disable()
        after = values[-1]
        expected_undo = (prior_undo + [{'elements': item['elements'], 'selection': item['selection']}
            for item in [before, *values[:-1]]])[-50:]
        actual_undo = json.loads(store.connection.execute('SELECT undo_history FROM documents WHERE id=?',
                                                        (document_id,)).fetchone()['undo_history'])
        history_steps = min(args.steps, 50)
        expected_undone = [before, *values][args.steps - history_steps]
        undo, undone = measure(lambda: store.history_step(document_id, 'undo'), history_steps)
        redo, redone = measure(lambda: store.history_step(document_id, 'redo'), history_steps)
        stats = pstats.Stats(profile)
        functions = sorted([{'function': f'{path}:{line}:{name}', 'calls': values[1],
            'own_ms': round(values[2] * 1000, 1), 'cumulative_ms': round(values[3] * 1000, 1)}
            for (path, line, name), values in stats.stats.items()], key=lambda item: item['cumulative_ms'], reverse=True)
        checks = {
            'all_history_snapshots_and_order_preserved': actual_undo == expected_undo,
            'undo_restores_retained_scene': all(undone[-1][key] == expected_undone[key] for key in ('elements', 'selection')),
            'redo_restores_edited_scene': all(redone[-1][key] == after[key] for key in ('elements', 'selection')),
            'other_documents_unchanged': unrelated == list(store.connection.execute(
                'SELECT * FROM documents WHERE id<>? ORDER BY id', (document_id,))),
        }
        result = {'source_database': str(args.source_database), 'database': str(args.database),
            'store_source_sha256': source_hash, 'sqlite_version': sqlite3.sqlite_version,
            'document_id': document_id, 'element_count': len(before['elements']),
            'scene_bytes': row['scene_bytes'], 'history_bytes': row['history_bytes'],
            'moves': moves, 'undo': undo, 'redo': redo, 'checks': checks,
            'profile': functions[:16], 'passed': all(checks.values())}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(result, ensure_ascii=False), flush=True)
        if not result['passed']:
            raise SystemExit(1)
    finally:
        store.close()


if __name__ == '__main__':
    main()
