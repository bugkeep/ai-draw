import asyncio
import base64
import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path


class CanvasRenderError(ValueError):
    pass


class CanvasRenderer:
    def __init__(self):
        self.process = None
        self.lock = asyncio.Lock()
        self.stderr_task = None
        self.errors = []

    async def _stderr(self, process):
        while line := await process.stderr.readline():
            self.errors.append(line.decode('utf-8', errors='replace').rstrip())
            del self.errors[:-8]

    async def _start(self):
        if self.process is not None and self.process.returncode is None:
            return
        executable = shutil.which('node')
        root = Path(__file__).resolve().parent.parent
        if not executable or not (root / 'frontend-dist/render.html').is_file():
            raise CanvasRenderError('画布图片服务需要 Node.js 和已构建的前端，请安装依赖并运行 npm run build。')
        self.process = await asyncio.create_subprocess_exec(
            executable, str(root / 'frontend-app/scripts/render_worker.mjs'), cwd=root,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, limit=16 * 1024 * 1024,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
        )
        self.errors = []
        self.stderr_task = asyncio.create_task(self._stderr(self.process))
        ready = await self.process.stdout.readline()
        if not ready or not json.loads(ready).get('ready'):
            await self.process.wait()
            raise CanvasRenderError('画布图片服务启动失败；请确认 Chrome 或 Playwright Chromium 已安装。' + ' '.join(self.errors)[-1000:])

    async def render(self, document: dict, region: dict | None = None) -> dict:
        async with self.lock:
            try:
                await self._start()
                request_id = uuid.uuid4().hex
                request = {'id': request_id, 'drawing': {'elements': document['elements'], 'region': region}}
                self.process.stdin.write((json.dumps(request, ensure_ascii=False) + '\n').encode('utf-8'))
                await self.process.stdin.drain()
                response = await self.process.stdout.readline()
                if not response:
                    raise CanvasRenderError('画布图片服务已结束。' + ' '.join(self.errors)[-500:])
                output = json.loads(response)
                if output.get('id') != request_id:
                    raise CanvasRenderError('画布图片返回编号不匹配。')
                if output.get('error'):
                    raise CanvasRenderError(output['error'])
                result = output['result']
                image = base64.b64decode(result.pop('image'), validate=True)
                if not image.startswith(b'\x89PNG\r\n\x1a\n'):
                    raise CanvasRenderError('画布图片服务未返回 PNG。')
                return {**result, 'png': image}
            except asyncio.CancelledError:
                await self._close()
                raise
            except (BrokenPipeError, ConnectionError, OSError) as error:
                await self._close()
                raise CanvasRenderError(str(error)) from error

    async def _close(self):
        process, reader = self.process, self.stderr_task
        self.process = self.stderr_task = None
        if process is not None and process.returncode is None:
            process.stdin.close()
            await process.wait()
        if reader is not None:
            await reader

    async def close(self):
        async with self.lock:
            await self._close()
