import { spawn } from 'node:child_process'
import { mkdir, writeFile } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..')
const output = resolve(root, process.env.DRAW_AGENT_EVAL_DIR || 'runs/quality-http-evaluation')
if (!process.env.DRAW_AGENT_URL || !process.env.DRAW_AGENT_PYTHON) {
  throw new Error('Run through python -m scripts.verify_browser with an isolated database')
}
await mkdir(output, { recursive: true })
await writeFile(resolve(output, 'server.json'), JSON.stringify({ url: process.env.DRAW_AGENT_URL,
  database: process.env.DRAW_AGENT_DB, source: 'owned_isolated_production_http_server' }, null, 2))
const args = ['-m', 'scripts.evaluate_quality', '--url', process.env.DRAW_AGENT_URL, '--output-dir', output]
if (process.env.DRAW_AGENT_QUALITY_CASES) args.push('--cases', process.env.DRAW_AGENT_QUALITY_CASES)
const worker = spawn(process.env.DRAW_AGENT_PYTHON, args, { cwd: root, env: process.env, stdio: 'inherit', windowsHide: true })
worker.on('error', (error) => { throw error })
process.exitCode = await new Promise((resolve) => worker.once('exit', (code) => resolve(code ?? 1)))
