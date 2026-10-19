import { spawn } from 'node:child_process'
import { copyFile, mkdir, writeFile } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..')
const output = resolve(root, process.env.DRAW_AGENT_EVAL_DIR || 'runs/quality-http-evaluation')
if (!process.env.DRAW_AGENT_URL || !process.env.DRAW_AGENT_PYTHON) {
  throw new Error('Run through python -m scripts.verify_browser with an isolated database')
}
await mkdir(output, { recursive: true })
await writeFile(resolve(output, 'server.json'), JSON.stringify({ url: process.env.DRAW_AGENT_URL,
  database: process.env.DRAW_AGENT_DB, source: 'owned_isolated_production_http_server' }, null, 2))
const runner = process.env.DRAW_AGENT_EVALUATOR || 'drawing-quality'
const runners = { 'drawing-quality': 'scripts.evaluate_quality',
  'reference-polygon': 'scripts.evaluate_reference_polygon', 'observed-coordinates': 'scripts.evaluate_observed_coordinates' }
if (!Object.hasOwn(runners, runner)) throw new Error('Unknown evaluation runner')
const args = ['-m', runners[runner],
  '--url', process.env.DRAW_AGENT_URL, '--output-dir', output]
if (runner !== 'drawing-quality') args.push('--database', process.env.DRAW_AGENT_DB)
else {
  if (process.env.DRAW_AGENT_QUALITY_CASES) args.push('--cases', process.env.DRAW_AGENT_QUALITY_CASES)
  if (process.env.DRAW_AGENT_QUALITY_DATASET) args.push('--dataset', process.env.DRAW_AGENT_QUALITY_DATASET)
}
const browser = await chromium.launch({ headless: true,
  ...(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } :
    process.platform === 'win32' ? { channel: 'chrome' } : {}),
})
let current
let lineBuffer = ''
let recordings = Promise.resolve()
const recordingErrors = []

async function closeRecording(status, result = {}) {
  if (!current) return
  const item = current
  current = undefined
  try {
    const saved = await item.context.request.get(`/api/studio/sessions/${item.session_id}`)
    if (!saved.ok()) throw new Error(`Read final canvas: HTTP ${saved.status()}`)
    const version = (await saved.json()).document.version
    await item.page.waitForFunction((value) => document.querySelector('.revision')?.textContent.startsWith(`版本 ${value} ·`), version)
    await item.page.screenshot({ path: resolve(output, `${item.case}.browser.png`) })
  } catch (error) {
    item.errors.push(error.message)
  } finally {
    const video = item.page.video()
    await item.context.close()
    const filename = `${item.case}.webm`
    await copyFile(await video.path(), resolve(output, filename))
    await writeFile(resolve(output, `${item.case}.recording.json`), JSON.stringify({
      case: item.case, session_id: item.session_id, task_id: item.task_id, status,
      video: filename, browser_errors: item.errors, source: 'live_editor_during_evaluation_task',
      model_context_count: result.model_context_count ?? null,
      recording_started_at: item.startedAt, received_scene_patches: item.events.scene_patch,
      received_stroke_progress: item.events.stroke_progress,
    }, null, 2))
    recordingErrors.push(...item.errors)
  }
}

async function processCase(value) {
  if (!value.case || !value.session_id || !value.task_id) return
  if (!/^[a-z0-9][a-z0-9_-]*$/i.test(value.case)) throw new Error('Use a simple evaluation case ID')
  if (value.status) {
    if (current?.task_id === value.task_id) await closeRecording(value.status, value)
    return
  }
  await closeRecording('interrupted')
  const context = await browser.newContext({ baseURL: process.env.DRAW_AGENT_URL,
    viewport: { width: 1440, height: 1000 },
    recordVideo: { dir: resolve(output, 'recording-source'), size: { width: 1440, height: 1000 } },
  })
  await context.addInitScript((id) => localStorage.setItem('draw-agent-session', id), value.session_id)
  const page = await context.newPage()
  const errors = []
  const events = { scene_patch: 0, stroke_progress: 0 }
  page.on('pageerror', (error) => errors.push(error.message))
  page.on('websocket', (socket) => socket.on('framereceived', ({ payload }) => {
    try {
      const event = JSON.parse(payload.toString())
      if (Object.hasOwn(events, event.type)) events[event.type] += 1
    } catch { /* Non-JSON transport frames carry no drawing actions. */ }
  }))
  current = { ...value, context, page, errors, events, startedAt: Date.now() / 1000 }
  await page.goto('/', { waitUntil: 'networkidle' })
}

try {
  const worker = spawn(process.env.DRAW_AGENT_PYTHON, args, {
    cwd: root, env: { ...process.env, PYTHONIOENCODING: 'utf-8' }, stdio: ['inherit', 'pipe', 'inherit'], windowsHide: true,
  })
  worker.stdout.setEncoding('utf8')
  worker.stdout.on('data', (chunk) => {
    process.stdout.write(chunk)
    lineBuffer += chunk
    let newline
    while ((newline = lineBuffer.indexOf('\n')) >= 0) {
      const line = lineBuffer.slice(0, newline).trim()
      lineBuffer = lineBuffer.slice(newline + 1)
      let value
      try { value = JSON.parse(line) } catch { continue }
      recordings = recordings.then(() => processCase(value)).catch((error) => recordingErrors.push(error.message))
    }
  })
  const code = await new Promise((resolve, reject) => {
    worker.once('error', reject)
    worker.once('exit', (code) => resolve(code ?? 1))
  })
  await recordings
  await closeRecording('interrupted')
  await writeFile(resolve(output, 'recording-errors.json'), JSON.stringify(recordingErrors, null, 2))
  process.exitCode = code || (recordingErrors.length ? 1 : 0)
} finally {
  await closeRecording('interrupted')
  await browser.close()
}
