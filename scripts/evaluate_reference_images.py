import argparse
import asyncio
import json
from pathlib import Path

from studio.reference_images import ReferenceImages
from studio.store import StudioStore


async def evaluate(args):
    if args.database.exists():
        raise ValueError('Use a new independent evaluation database')
    store = StudioStore(args.database)
    images = ReferenceImages(store)
    reports = []
    try:
        for case in [
            {'query': 'Mona Lisa drawing tutorial', 'subject_terms': ['Mona Lisa'], 'goal': '人物的构图、面部与手部关系'},
            {'query': 'how to draw mountains tutorial', 'subject_terms': ['mountain'], 'goal': '山水的远近关系与山体轮廓'},
            {'query': 'how to draw a sitting cat tutorial', 'subject_terms': ['cat'], 'goal': '坐姿猫的身体轮廓与四肢关系'},
        ]:
            session = store.create_session(case['goal'])
            task = store.create_task(session['id'])
            try:
                result = await images.search(task['id'], 'reference-evaluation', case)
            except ValueError as error:
                result = {'ok': False, 'error': str(error)}
            report = {'case': case, 'session_id': session['id'], 'task_id': task['id'], 'result': result}
            if result.get('ok'):
                source = result['sources'][0]
                png = images.image(session['id'], source['reference_id'])
                report['checks'] = {
                    'decoded_actual_png': png.startswith(b'\x89PNG\r\n\x1a\n'),
                    'reference_separate_from_canvas': store.get_document(session['document']['id'])['elements'] == [],
                    'source_resolution': min(source['width'], source['height']) >= 128,
                    'cache_reused': (await images.search(task['id'], 'reference-evaluation', case)).get('cached') is True,
                }
                other = store.create_session('独立参考检查')
                other_task = store.create_task(other['id'])
                try:
                    images.view(other_task['id'], 'other', {'reference_id': source['reference_id']})
                except KeyError:
                    report['checks']['canvas_isolation'] = True
                else:
                    report['checks']['canvas_isolation'] = False
                caller = f"main:{task['id']}"
                images.view(task['id'], caller, {'reference_id': source['reference_id']})
                _, views = images.context(task['id'], caller)
                report['checks']['actual_image_context'] = len(views) == 1
            reports.append(report)
            args.output.write_text(json.dumps({'cases': reports, 'passed': all(
                item['result'].get('ok') and all(item['checks'].values()) for item in reports)},
                ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps(report), flush=True)
        return all(item['result'].get('ok') and all(item['checks'].values()) for item in reports)
    finally:
        store.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--database', type=Path, default=Path('runs/reference-images-evaluation.sqlite3'))
    parser.add_argument('--output', type=Path, default=Path('runs/reference-images-evaluation.json'))
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if not asyncio.run(evaluate(args)):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
