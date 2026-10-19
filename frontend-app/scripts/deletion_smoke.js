import { chromium } from 'playwright'

const baseURL = process.env.DRAW_AGENT_URL || 'http://127.0.0.1:8000'
const browser = await chromium.launch({ headless: true,
  ...(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : process.platform === 'win32' ? { channel: 'chrome' } : {}),
})
const context = await browser.newContext({ baseURL, viewport: { width: 1440, height: 900 } })
const page = await context.newPage()
const errors = []
page.on('pageerror', (error) => errors.push(error.message))

async function request(method, path, data) {
  const response = await context.request[method](`/api/studio${path}`, { data })
  if (!response.ok()) throw new Error(`${method} ${path}: HTTP ${response.status()}`)
  return response.json()
}

async function waitTask(id, status = 'completed') {
  for (let attempt = 0; attempt < 200; attempt += 1) {
    const task = await request('get', `/tasks/${id}`)
    if (task.status === status) return task
    if (['failed', 'partial'].includes(task.status)) throw new Error(`Task failed: ${task.error}`)
    await page.waitForTimeout(30)
  }
  throw new Error(`Task did not reach ${status}`)
}

async function openDelete(id) {
  const row = page.locator(`[data-session-id="${id}"]`).locator('..')
  await row.getByRole('button', { name: '删除画布', exact: true }).click()
  return page.getByRole('dialog', { name: '删除画布会话', exact: true })
}

try {
  const existing = await request('get', '/sessions')
  const first = await request('post', '/sessions', { title: `删除检查 A ${Date.now()}` })
  const second = await request('post', '/sessions', { title: `保留检查 B ${Date.now()}` })
  const background = await request('post', '/sessions', { title: '后台空画布' })
  for (const [session, message] of [[first, '画一个蓝色圆形'], [second, '画一个红色矩形']]) {
    const task = await request('post', `/sessions/${session.id}/messages`, { message })
    await waitTask(task.id)
  }
  const secondBefore = await request('get', `/sessions/${second.id}`)
  await page.goto('/', { waitUntil: 'networkidle' })
  await page.evaluate((id) => localStorage.setItem('draw-agent-session', id), first.id)
  await page.reload({ waitUntil: 'networkidle' })
  await page.waitForSelector(`[data-session-id="${first.id}"].selected`)
  const viewer = await context.newPage()
  viewer.on('pageerror', (error) => errors.push(error.message))
  await viewer.goto('/', { waitUntil: 'networkidle' })
  await viewer.waitForSelector(`[data-session-id="${first.id}"].selected`)
  if (await page.locator('.backend-notice').count()) throw new Error('Current backend was rejected')
  let dialog = await openDelete(first.id)
  if (!(await dialog.textContent()).includes(first.title)) throw new Error('Confirmation has the wrong title')
  await dialog.getByRole('button', { name: '取消', exact: true }).click()
  if ((await request('get', `/sessions/${first.id}`)).messages.length !== 2) throw new Error('Cancel changed the session')

  dialog = await openDelete(background.id)
  await dialog.getByRole('button', { name: '确认删除', exact: true }).click()
  await page.waitForSelector(`[data-session-id="${background.id}"]`, { state: 'detached' })
  if (!(await page.locator(`[data-session-id="${first.id}"]`).getAttribute('aria-current'))) throw new Error('Deleting a background session changed the canvas')

  dialog = await openDelete(first.id)
  await dialog.getByRole('button', { name: '确认删除', exact: true }).click()
  for (const target of [page, viewer]) {
    await target.waitForFunction((id) => {
      const selected = document.querySelector('.session-item.selected')?.dataset.sessionId
      return selected && selected !== id
    }, first.id)
    await target.locator(`[data-session-id="${second.id}"]`).click()
    await target.waitForSelector(`[data-session-id="${second.id}"].selected`)
    await target.waitForFunction((id) => localStorage.getItem('draw-agent-session') === id, second.id)
    if ((await target.locator('.bubble.user').count()) !== 1) throw new Error('Deleted conversation entered the remaining canvas')
  }
  const secondAfter = await request('get', `/sessions/${second.id}`)
  const content = (session) => ({ title: session.title, messages: session.messages, tasks: session.tasks,
    elements: session.document.elements.map(({ id, type, x, y, width, height, backgroundColor, strokeColor, customData }) =>
      ({ id, type, x, y, width, height, backgroundColor, strokeColor, customData })) })
  if (JSON.stringify(content(secondBefore)) !== JSON.stringify(content(secondAfter))) throw new Error('Deleting A changed B')
  const deleted = await context.request.get(`/api/studio/sessions/${first.id}`)
  if (deleted.status() !== 404) throw new Error('Deleted session still exists')
  await page.reload({ waitUntil: 'networkidle' })
  await page.waitForSelector(`[data-session-id="${second.id}"].selected`)
  await viewer.close()

  await page.getByRole('textbox', { name: '绘图指令' }).fill('用画笔画一张笑脸')
  await page.getByRole('button', { name: '发送 ↑', exact: true }).click()
  await page.getByRole('button', { name: '停止', exact: true }).waitFor()
  dialog = await openDelete(second.id)
  if (!(await dialog.getByRole('button', { name: '确认删除', exact: true }).isDisabled())) throw new Error('Running task can be deleted')
  await dialog.getByRole('button', { name: '取消', exact: true }).click()
  await page.getByRole('button', { name: '停止', exact: true }).click()
  await page.getByRole('button', { name: '停止', exact: true }).waitFor({ state: 'detached' })
  dialog = await openDelete(second.id)
  await dialog.getByRole('button', { name: '确认删除', exact: true }).click()
  await page.waitForSelector(`[data-session-id="${second.id}"]`, { state: 'detached' })
  await page.waitForSelector('.session-item.selected')
  const remainingId = await page.locator('.session-item.selected').getAttribute('data-session-id')
  const remaining = await request('get', `/sessions/${remainingId}`)
  if (existing.length === 0 && (remaining.messages.length || remaining.document.elements.length)) throw new Error('Last deleted canvas was not replaced with an empty canvas')
  if (errors.length) throw new Error(errors.join('\n'))
  console.log(JSON.stringify({ confirmation_cancel: true, delete_background: true, delete_current: true,
    deleted_data_removed: true, other_session_preserved: true, viewer_recovers: true, refresh_after_delete: true,
    running_task_guard: true, delete_after_cancel: true, last_canvas_replacement: existing.length === 0 }))
} finally {
  await context.close()
  await browser.close()
}
