import hashlib
import shutil
from pathlib import Path


def capture_implementation(output_dir, *, snapshot=True):
    root = Path(__file__).resolve().parent.parent
    files = [path for package in ('studio', 'server') for path in sorted((root / package).rglob('*.py'))]
    files.extend(root / name for name in ('requirements.txt', 'requirements-dev.txt',
        'frontend-app/src/renderCanvas.js', 'frontend-app/package.json', 'frontend-app/package-lock.json',
        'scripts/evaluation_artifacts.py', 'scripts/evaluate_quality.py', 'scripts/evaluate_reference_polygon.py',
        'scripts/evaluate_prompt_context.py',
        'scripts/verify_browser.py', 'frontend-app/scripts/quality_evaluation.js'))
    implementation = {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in files}
    if snapshot:
        for path in files:
            destination = output_dir / 'implementation' / path.relative_to(root)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, destination)
    return implementation
