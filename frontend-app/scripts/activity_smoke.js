import { chromium } from 'playwright'

const baseUrl = process.env.DRAW_AGENT_URL || 'http://127.0.0.1:8000'
const browser = await chromium.launch({ headless: true,
  ...(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : process.platform === 'win32' ? { channel: 'chrome' } : {}),
})
const context = await browser.newContext({ baseURL: baseUrl, viewport: { width: 1440, height: 900 } })
const page = await context.newPage()
const errors = []
page.on('pageerror', (error) => errors.push(error.message))
let taskId

async function read(path) {
  const response = await context.request.get(path)
  if (!response.ok()) throw new Error(`Read ${path}: ${response.status()}`)
  return response.json()
}

try {
  if (!(await read('/api/studio/health')).provider_configured) throw new Error('This test requires a configured real model')
  const response = await context.request.post('/api/studio/sessions', { data: { title: '任务状态与停止检查' } })
  if (!response.ok()) throw new Error('Could not create test canvas')
  const session = await response.json()
  const otherResponse = await context.request.post('/api/studio/sessions', { data: { title: '状态隔离检查' } })
  if (!otherResponse.ok()) throw new Error('Could not create the second canvas')
  const other = await otherResponse.json()
  await page.goto('/', { waitUntil: 'networkidle' })
  await page.locator(`[data-session-id="${session.id}"]`).click()
  await page.getByRole('textbox', { name: '绘图指令' }).fill('画一幅海港城市的建筑素描，包含码头、帆船和临海街道。')
  const sent = page.waitForResponse((item) => item.url().endsWith(`/sessions/${session.id}/messages`) && item.request().method() === 'POST')
  await page.getByRole('button', { name: '发送 ↑' }).click()
  taskId = (await (await sent).json()).id
  await page.locator('.activity-time').waitFor()
  await page.waitForFunction(() => document.querySelector('.activity-time')?.textContent.match(/已用时 0:0[2-9]/))
  await page.reload({ waitUntil: 'networkidle' })
  await page.locator('.activity-time').waitFor()
  if (!(await page.locator('.activity-description').textContent()).includes('用时')) throw new Error('Task elapsed time was lost after reload')
  const beforeSwitch = await read(`/api/studio/sessions/${session.id}`)
  const phaseText = await page.locator('.activity-description > span').first().textContent()
  if (!beforeSwitch.activity || phaseText === '正在绘图') throw new Error('The saved model phase was not restored after reload')
  await page.locator(`[data-session-id="${other.id}"]`).click()
  await page.waitForSelector(`[data-session-id="${other.id}"].selected`)
  if (await page.locator('.activity').count()) throw new Error('Task activity entered another canvas')
  await page.locator(`[data-session-id="${session.id}"]`).click()
  await page.waitForSelector(`[data-session-id="${session.id}"].selected`)
  await page.locator('.activity-time').waitFor()
  const afterSwitch = await read(`/api/studio/sessions/${session.id}`)
  if (beforeSwitch.activity.phase !== afterSwitch.activity?.phase ||
      beforeSwitch.activity.agent_id !== afterSwitch.activity?.agent_id) {
    throw new Error('The task phase changed during the observation; repeat this check')
  }
  if (await page.locator('.activity-description > span').first().textContent() !== phaseText) {
    throw new Error('Switching back to the running canvas discarded its saved phase')
  }
  const stop = page.locator('.activity').getByRole('button', { name: '停止', exact: true })
  await stop.click()
  await page.waitForFunction(() => !document.querySelector('.activity'))
  const task = await read(`/api/studio/tasks/${taskId}`)
  if (task.status !== 'cancelled') throw new Error(`Task status ${task.status}`)
  const repeated = await context.request.post(`/api/studio/tasks/${taskId}/cancel`)
  if (!repeated.ok()) throw new Error('Repeated stop request was not idempotent')
  const stored = await read(`/api/studio/sessions/${session.id}`)
  if (stored.activity !== null) throw new Error('A stopped task retained an active phase')
  if (errors.length) throw new Error(errors.join('\n'))
  console.log(JSON.stringify({ elapsed_time: true, phase_after_reload: true, phase_after_switch: true,
    other_canvas_has_no_activity: true, stop_completes: true,
    repeated_stop: true, terminal_phase_cleared: true, no_browser_errors: true }))
} finally {
  if (taskId) {
    const task = await read(`/api/studio/tasks/${taskId}`)
    if (['running', 'queued'].includes(task.status)) await context.request.post(`/api/studio/tasks/${taskId}/cancel`)
  }
  await context.close()
  await browser.close()
}
