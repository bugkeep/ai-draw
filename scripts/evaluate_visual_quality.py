import argparse
import asyncio
import base64
import json
import time
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field

from studio.llm import ModelGateway, close_model_clients
from studio.renderer import CanvasRenderer
from studio.settings import ModelSettings
from studio.store import StudioStore


class VisualCriterion(BaseModel):
    model_config = ConfigDict(extra='forbid')
    criterion: str
    passed: bool
    evidence: str


class VisualAssessment(BaseModel):
    model_config = ConfigDict(extra='forbid')
    visible_subject: str
    visible_relationships: list[str]
    defects: list[str]
    criteria: list[VisualCriterion] = Field(min_length=1)
    ready_for_review: bool


async def evaluate(args):
    store = StudioStore(args.database)
    renderer = CanvasRenderer()
    config = ModelSettings(store).load()
    gateway = ModelGateway(config)
    results = []
    try:
        for path in args.inputs:
            if path.suffix.lower() == '.excalidraw':
                native = json.loads(path.read_text(encoding='utf-8'))
                rendered = await renderer.render(native)
                png = rendered['png']
                image_path = args.output.with_name(f'{args.output.stem}-{path.stem}.png')
                image_path.write_bytes(png)
            elif path.suffix.lower() == '.png':
                png = path.read_bytes()
                image_path = path
            else:
                raise ValueError('Choose a PNG or native Excalidraw document')
            image = {'type': 'image_url', 'image_url': {
                'url': 'data:image/png;base64,' + base64.b64encode(png).decode('ascii'), 'detail': 'high'}}
            started = time.perf_counter()
            blind = await gateway.complete([{'role': 'user', 'content': [
                {'type': 'text', 'text': 'Describe only the visible drawing: subject, pose, spatial relationships and obvious visual defects. '
                                         'Do not infer a title or the intended subject from metadata. Reply in Chinese within 180 words.'}, image]}])
            description = blind.choices[0].message.content
            if not description:
                raise ValueError('Visual reviewer returned no image description')
            response = await gateway.complete([{'role': 'user', 'content': [
                {'type': 'text', 'text': json.dumps({'drawing_request': args.prompt,
                    'criteria': args.rubric or ['主体可以辨认', '比例与连接关系自然', '线条清楚且没有重复轮廓', '构图完整'],
                    'instruction': 'Assess only visible pixels against each criterion. Identify concrete visible defects, including overlaps, anatomy and missing parts. '
                                   'A recognizable rough symbol is not a finished artwork. Report through report_visual_quality with evidence for each criterion. '
                                   'Set ready_for_review only when the requested drawing has no major visible defect. Reply in Chinese.'}, ensure_ascii=False)}, image]}],
                [{'type': 'function', 'function': {'name': 'report_visual_quality',
                    'description': 'Report the visual assessment with evidence from the actual image.',
                    'parameters': VisualAssessment.model_json_schema()}}])
            calls = response.choices[0].message.tool_calls or []
            if len(calls) != 1 or calls[0].function.name != 'report_visual_quality':
                raise ValueError('Visual reviewer did not return the requested assessment')
            assessment = VisualAssessment.model_validate_json(calls[0].function.arguments)
            result = {'input': str(path), 'image': str(image_path), 'blind_description': description,
                      'assessment': assessment.model_dump(), 'latency_ms': round((time.perf_counter() - started) * 1000, 1)}
            results.append(result)
            args.output.write_text(json.dumps({'provider': config.provider, 'model': config.model,
                'drawing_request': args.prompt, 'results': results,
                'assessment_type': 'model_assisted_visual_review'}, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps(result, ensure_ascii=False), flush=True)
        return all(item['assessment']['ready_for_review'] and all(criterion['passed'] for criterion in item['assessment']['criteria'])
                   for item in results)
    finally:
        await renderer.close()
        await close_model_clients()
        store.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('inputs', nargs='+', type=Path)
    parser.add_argument('--prompt', default='')
    parser.add_argument('--rubric', action='append', default=[])
    parser.add_argument('--database', type=Path, default=Path('runs/visual-quality.sqlite3'))
    parser.add_argument('--output', type=Path, default=Path('runs/visual-quality.json'))
    args = parser.parse_args()
    if args.database.resolve() == Path('runs/studio.sqlite3').resolve():
        raise ValueError('Use a separate evaluation database')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    load_dotenv(Path(__file__).resolve().parent.parent / '.env', override=False)
    if not asyncio.run(evaluate(args)):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
