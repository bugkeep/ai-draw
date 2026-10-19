import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

from studio.reference_images import ReferenceImages
from studio.store import StudioStore


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-database', type=Path, required=True)
    parser.add_argument('--reference-id', action='append', required=True)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.database.exists():
        raise ValueError('Use a new independent evaluation database')
    references = []
    with sqlite3.connect(f'{args.source_database.resolve().as_uri()}?mode=ro', uri=True) as source:
        source.row_factory = sqlite3.Row
        for reference_id in dict.fromkeys(args.reference_id):
            row = source.execute('SELECT * FROM reference_images WHERE id=?', (reference_id,)).fetchone()
            if row is None:
                raise ValueError(f'Actual saved reference image not found: {reference_id}')
            references.append(dict(row))
    store = StudioStore(args.database)
    images = ReferenceImages(store)
    try:
        session = store.create_session('已保存参考图 · 实际作画验证')
        task = store.create_task(session['id'])
        imported = []
        for reference in references:
            metadata = json.loads(reference['metadata'])
            saved = images.save_image(task['id'], 'reference-import', {
                'title': metadata['title'], 'url': metadata['url'], 'image': metadata['image_url'],
                'license': metadata.get('license', 'unverified'), 'description': metadata.get('description', ''),
            }, reference['original'], (reference['png'], metadata))
            imported.append({'source_reference_id': reference['id'], 'reference_id': saved['reference_id'],
                'title': saved['title'], 'png_sha256': hashlib.sha256(reference['png']).hexdigest()})
        store.update_task(task['id'], 'completed', '评测参考图片导入完成')
        root = Path(__file__).resolve().parent.parent
        implementation = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in (
            'studio/agents.py', 'studio/reference_images.py', 'studio/runtime.py', 'studio/agent_manager.py',
            'studio/actions.py', 'studio/pen.py', 'studio/memory.py', 'studio/store.py')}
        result = {'session_id': session['id'], 'database': str(args.database), 'source_database': str(args.source_database),
            'imported': imported, 'implementation_sha256': implementation,
            'scope': 'actual saved reference reuse; does not measure fresh public search availability'}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(result), flush=True)
    finally:
        store.close()


if __name__ == '__main__':
    main()
