import { chromium } from 'playwright'
import { copyFile, mkdir, writeFile } from 'node:fs/promises'
import { resolve } from 'node:path'

const baseUrl = process.env.DRAW_AGENT_URL || 'http://127.0.0.1:8000'
const recordingName = process.env.DRAW_AGENT_RECORDING_NAME || 'portrait-recorded'
if (!/^[a-z0-9_-]+$/i.test(recordingName)) throw new Error('Use a simple recording filename')
const root = resolve('..')
const directory = resolve(root, 'runs', 'portrait-recording')
await mkdir(directory, { recursive: true })
const browser = await chromium.launch({ headless: true,
  ...(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : process.platform === 'win32' ? { channel: 'chrome' } : {}),
})
const context = await browser.newContext({ baseURL: baseUrl, viewport: { width: 1440, height: 1000 },
  recordVideo: { dir: directory, size: { width: 1440, height: 1000 } } })
const page = await context.newPage()
const errors = []
page.on('pageerror', (error) => errors.push(error.message))
let taskId
let finished = false
try {
  const response = await context.request.post('/api/studio/sessions', { data: { title: '蒙娜丽莎 · 实际作画录像' } })
  if (!response.ok()) throw new Error('Could not create portrait session')
  const session = await response.json()
  await page.goto('/', { waitUntil: 'networkidle' })
  await page.locator(`[data-session-id="${session.id}"]`).click()
  const prompt = '画一幅蒙娜丽莎素描图'
  await page.getByRole('textbox', { name: '绘图指令' }).fill(prompt)
  const sent = page.waitForResponse((item) => item.url().endsWith(`/sessions/${session.id}/messages`) && item.request().method() === 'POST')
  await page.getByRole('button', { name: '发送 ↑' }).click()
  taskId = (await (await sent).json()).id
  console.log(JSON.stringify({ session_id: session.id, task_id: taskId, prompt }))
  let task
  let lastPhase
  while (true) {
    const response = await context.request.get(`/api/studio/tasks/${taskId}`)
    if (!response.ok()) throw new Error(`Task read ${response.status()}`)
    task = await response.json()
    const phase = await page.locator('.activity-description').evaluateAll((items) => items[0]?.firstElementChild?.textContent || '')
    if (phase !== lastPhase) {
      console.log(JSON.stringify({ status: task.status, phase }))
      lastPhase = phase
    }
    if (['completed', 'partial', 'failed', 'cancelled'].includes(task.status)) {
      finished = true
      break
    }
    await page.waitForTimeout(1000)
  }
  await page.waitForFunction(() => !document.querySelector('.activity'))
  const downloadPromise = page.waitForEvent('download')
  await page.getByRole('button', { name: '导出作品' }).click()
  await page.getByRole('button', { name: '下载文件' }).click()
  const download = await downloadPromise
  if (await download.failure()) throw new Error(await download.failure())
  const png = resolve(root, 'runs', `${recordingName}.png`)
  await download.saveAs(png)
  const video = page.video()
  await context.close()
  const output = resolve(root, 'runs', `${recordingName}.webm`)
  await copyFile(await video.path(), output)
  const report = { session_id: session.id, task_id: taskId, prompt, status: task.status,
    latency_ms: task.latency_ms, output, png, browser_errors: errors }
  await writeFile(resolve(root, 'runs', `${recordingName}.json`), JSON.stringify(report, null, 2), 'utf8')
  console.log(JSON.stringify(report))
  if (task.status !== 'completed' || errors.length) throw new Error(`Portrait recording did not pass: ${task.status}`)
} finally {
  if (taskId && !finished) {
    const response = await context.request.get(`/api/studio/tasks/${taskId}`)
    if (response.ok() && ['running', 'queued'].includes((await response.json()).status)) {
      await context.request.post(`/api/studio/tasks/${taskId}/cancel`)
    }
  }
  if (context.pages().length) await context.close()
  await browser.close()
}
