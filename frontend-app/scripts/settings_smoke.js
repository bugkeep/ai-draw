import { chromium } from 'playwright'

const baseUrl = process.env.DRAW_AGENT_URL || 'http://127.0.0.1:8000'
const browser = await chromium.launch({ headless: true,
  ...(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : process.platform === 'win32' ? { channel: 'chrome' } : {}),
})
const context = await browser.newContext({ baseURL: baseUrl, viewport: { width: 1440, height: 900 } })
const page = await context.newPage()
const errors = []
page.on('pageerror', (error) => errors.push(error.message))
const sockets = []
page.on('websocket', (socket) => {
  const entry = { url: socket.url(), frames: [], closed: false }
  sockets.push(entry)
  socket.on('framereceived', ({ payload }) => {
    const event = JSON.parse(String(payload))
    entry.frames.push({ type: event.type, document_id: event.document_id })
  })
  socket.on('close', () => { entry.closed = true })
})

async function request(path, data) {
  const response = data === undefined ? await context.request.get(path) : await context.request.post(path, { data })
  if (!response.ok()) throw new Error(`Request ${path}: HTTP ${response.status()}`)
  return response.json()
}

async function waitForTask(id) {
  for (let attempt = 0; attempt < 150; attempt += 1) {
    const task = await request(`/api/studio/tasks/${id}`)
    if (task.status === 'completed') return task
    if (['failed', 'partial', 'cancelled'].includes(task.status)) throw new Error(`Task ${id}: ${task.status} ${task.error}`)
    await page.waitForTimeout(100)
  }
  throw new Error(`Task ${id} exceeded the test timeout`)
}

async function switchTo(session, target = page) {
  await target.locator(`[data-session-id="${session.id}"]`).click()
  await target.waitForFunction((id) => document.querySelector(`.session-item.selected[data-session-id="${id}"]`), session.id)
}

try {
  const first = await request('/api/studio/sessions', { title: `浏览器隔离 A ${Date.now()}` })
  const second = await request('/api/studio/sessions', { title: `浏览器隔离 B ${Date.now()}` })
  await page.goto('/', { waitUntil: 'networkidle' })
  await page.waitForSelector('.session-item.selected')
  await switchTo(first)
  await page.getByRole('textbox', { name: '绘图指令' }).fill('用画笔画一张笑脸')
  const sent = page.waitForResponse((response) => response.url().endsWith(`/sessions/${first.id}/messages`) && response.request().method() === 'POST')
  await page.getByRole('button', { name: '发送 ↑' }).click()
  const taskA = await (await sent).json()
  await switchTo(second)
  await page.getByRole('textbox', { name: '绘图指令' }).fill('画一个蓝色圆形')
  const sentB = page.waitForResponse((response) => response.url().endsWith(`/sessions/${second.id}/messages`) && response.request().method() === 'POST')
  await page.getByRole('button', { name: '发送 ↑' }).click()
  await waitForTask((await (await sentB).json()).id)
  await waitForTask(taskA.id)
  await page.waitForTimeout(300)
  if (await page.getByText('用画笔画一张笑脸', { exact: true }).count()) throw new Error('Session A message appeared in session B')
  const revisionB = await page.locator('.revision').textContent()
  if (!revisionB.includes('1 个对象')) {
    const dataB = await request(`/api/studio/sessions/${second.id}`)
    throw new Error(`Session B scene: UI ${revisionB}; stored objects ${dataB.document.elements.length}; browser errors ${errors.join('; ')}; sockets ${JSON.stringify(sockets)}`)
  }

  const taskPage = await context.newPage()
  taskPage.on('pageerror', (error) => errors.push(error.message))
  await taskPage.goto('/', { waitUntil: 'networkidle' })
  await switchTo(first, taskPage)
  await taskPage.getByRole('textbox', { name: '绘图指令' }).fill('用画笔画一张笑脸')
  const nextResponse = taskPage.waitForResponse((response) => response.url().endsWith(`/sessions/${first.id}/messages`) && response.request().method() === 'POST')
  await taskPage.getByRole('button', { name: '发送 ↑' }).click()
  const nextA = await (await nextResponse).json()
  if (!(await page.locator('.revision').textContent()).includes('1 个对象')) throw new Error('Another browser window changed this scene')
  await page.locator('.top-actions').getByRole('button', { name: '模型设置' }).click()
  await page.waitForURL('**/settings')
  const save = page.getByRole('button', { name: '保存配置' })
  await page.waitForFunction(() => [...document.querySelectorAll('button')].some((button) => button.textContent === '保存配置' && button.disabled))
  const original = await request('/api/studio/settings')
  const rejected = await context.request.put('/api/studio/settings', { data: { ...original.config, revision: original.revision } })
  if (rejected.status() !== 409) throw new Error('Settings changed while another session was drawing')
  await waitForTask(nextA.id)
  await save.waitFor({ state: 'visible' })
  await page.waitForFunction(() => [...document.querySelectorAll('button')].some((button) => button.textContent === '保存配置' && !button.disabled))
  if (await page.getByLabel('API Key', { exact: true }).inputValue()) throw new Error('Saved secret was returned to the form')
  if (!original.configured) {
    await page.getByLabel('模型厂商', { exact: true }).selectOption('local')
    await page.getByLabel('模型名称', { exact: true }).fill('local-drawing-model')
  }
  const testModel = process.env.DRAW_AGENT_TEST_MODEL === '1'
  if (testModel) {
    if (!original.configured) throw new Error('Configure a real model before testing the connection')
    await page.getByRole('button', { name: '测试连接', exact: true }).click()
    await page.getByRole('status').filter({ hasText: '连接成功：文本响应与工具调用均通过' }).waitFor({ timeout: 120000 })
  }
  await save.click()
  await page.getByText('配置已保存，后续绘图将使用此模型。', { exact: true }).waitFor()
  const stored = await request('/api/studio/settings')
  if (stored.revision !== original.revision + 1) throw new Error('Configuration was not saved')
  await page.reload({ waitUntil: 'networkidle' })
  if (await page.getByLabel('API Key', { exact: true }).inputValue()) throw new Error('Reload exposed the saved secret')
  await taskPage.close()
  await page.getByRole('link', { name: '← 返回画布' }).click()
  await page.waitForURL(baseUrl.endsWith('/') ? baseUrl : `${baseUrl}/`)
  await page.waitForSelector('.session-item.selected')
  const sceneB = await request(`/api/studio/sessions/${second.id}`)
  const sceneA = await request(`/api/studio/sessions/${first.id}`)
  if (sceneB.document.elements.length !== 1 || sceneA.document.elements.length !== 8) throw new Error('Session scenes were mixed')
  await switchTo(first)
  await page.waitForFunction(() => document.querySelector('.revision')?.textContent.includes('8 个对象'))
  await switchTo(second)
  await page.waitForFunction(() => document.querySelector('.revision')?.textContent.includes('1 个对象'))
  if (errors.length) throw new Error(errors.join('\n'))
  console.log(JSON.stringify({ settings_navigation: true, saved_config: true, secret_not_returned: true,
    save_blocked_during_drawing: true, session_scene_isolation: true, session_message_isolation: true,
    browser_window_isolation: true, model_connection_tested: testModel }))
} finally {
  await context.close()
  await browser.close()
}
