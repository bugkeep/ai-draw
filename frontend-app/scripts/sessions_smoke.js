import { chromium } from 'playwright'

const baseUrl = process.env.DRAW_AGENT_URL || 'http://127.0.0.1:8000'
const browser = await chromium.launch({ headless: true,
  ...(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : process.platform === 'win32' ? { channel: 'chrome' } : {}),
})
const context = await browser.newContext({ baseURL: baseUrl, viewport: { width: 1440, height: 900 } })
const page = await context.newPage()
const errors = []
page.on('pageerror', (error) => errors.push(error.message))

async function request(path, data) {
  const response = data === undefined ? await context.request.get(path) : await context.request.post(path, { data })
  if (!response.ok()) throw new Error(`Request ${path}: HTTP ${response.status()}`)
  return response.json()
}

async function waitTask(id) {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    const task = await request(`/api/studio/tasks/${id}`)
    if (task.status === 'completed') return
    if (['failed', 'partial', 'cancelled'].includes(task.status)) throw new Error(`Task ${id}: ${task.status} ${task.error}`)
    await page.waitForTimeout(50)
  }
  throw new Error(`Task ${id} exceeded the test timeout`)
}

async function measure() {
  return page.evaluate(() => {
    const bounds = (selector) => {
      const element = document.querySelector(selector)
      const rect = element.getBoundingClientRect()
      return { top: rect.top, bottom: rect.bottom, height: rect.height, width: rect.width,
        scrollHeight: element.scrollHeight, clientHeight: element.clientHeight, scrollTop: element.scrollTop }
    }
    return { height: innerHeight, width: innerWidth, bodyHeight: document.documentElement.scrollHeight,
      bodyWidth: document.documentElement.scrollWidth, scrollY,
      list: bounds('.session-list'), conversation: bounds('.conversation'),
      composer: bounds('.composer'), canvas: bounds('.editor-host') }
  })
}

try {
  const marker = Date.now()
  const first = await request('/api/studio/sessions', { title: `滚动与名称检查 ${marker}` })
  for (let index = 0; index < 35; index += 1) {
    await request('/api/studio/sessions', { title: `${index} ${'连续绘图会话'.repeat(12)}` })
  }
  const initialTask = await request(`/api/studio/sessions/${first.id}/messages`, { message: '画一个蓝色圆形' })
  await waitTask(initialTask.id)
  for (let index = 0; index < 24; index += 1) {
    const task = await request(`/api/studio/sessions/${first.id}/messages`, { message: '把它向右移动 1 像素' })
    await waitTask(task.id)
  }
  await context.addInitScript((id) => localStorage.setItem('draw-agent-session', id), first.id)
  await page.goto('/', { waitUntil: 'networkidle' })
  await page.waitForSelector('.session-item.selected')
  const before = await measure()
  if (before.bodyHeight > before.height + 1 || before.bodyWidth > before.width + 1) throw new Error(`Page exceeds viewport: ${JSON.stringify(before)}`)
  if (before.list.scrollHeight <= before.list.clientHeight || before.list.bottom > before.height) throw new Error(`Session list does not scroll inside viewport: ${JSON.stringify(before)}`)
  if (before.conversation.scrollHeight <= before.conversation.clientHeight || before.composer.bottom > before.height || before.canvas.height < 300) throw new Error(`Conversation or canvas is outside viewport: ${JSON.stringify(before)}`)
  await page.locator('.session-list').hover()
  await page.mouse.wheel(0, 1200)
  await page.waitForFunction(() => document.querySelector('.session-list').scrollTop > 0)
  const scrolled = await measure()
  if (scrolled.scrollY !== 0 || Math.abs(scrolled.canvas.height - before.canvas.height) > 1) throw new Error('Scrolling the list moved the page or resized the canvas')
  await page.locator('.conversation').hover()
  await page.mouse.wheel(0, -1200)
  await page.waitForFunction((previous) => document.querySelector('.conversation').scrollTop < previous, before.conversation.scrollTop)

  const target = page.locator(`[data-session-id="${first.id}"]`)
  await target.scrollIntoViewIfNeeded()
  const viewer = await context.newPage()
  viewer.on('pageerror', (error) => errors.push(error.message))
  await viewer.goto('/', { waitUntil: 'networkidle' })
  await viewer.waitForSelector('.session-item.selected')
  await target.locator('..').getByRole('button', { name: '重命名', exact: true }).click()
  const input = page.getByRole('textbox', { name: '画布名称', exact: true })
  await input.fill('取消的名称')
  await input.press('Escape')
  if ((await request(`/api/studio/sessions/${first.id}`)).title !== first.title) throw new Error('Cancel renamed the session')
  await target.locator('..').getByRole('button', { name: '重命名', exact: true }).click()
  await input.fill('   ')
  await input.press('Enter')
  await page.getByRole('alert').filter({ hasText: '请输入画布名称' }).waitFor()
  const title = `公园设计 ${marker}`
  await input.fill(title)
  await input.press('Enter')
  await page.waitForFunction((value) => document.querySelector('.canvas-title')?.textContent === value, title)
  await viewer.waitForFunction((value) => document.querySelector('.canvas-title')?.textContent === value, title)
  const renamed = await request(`/api/studio/sessions/${first.id}`)
  if (renamed.document.id !== first.document.id || renamed.document.elements.length !== 1 || renamed.messages.length !== 50) throw new Error('Rename changed session content')
  await page.reload({ waitUntil: 'networkidle' })
  await page.waitForFunction((value) => document.querySelector('.canvas-title')?.textContent === value, title)

  await page.getByRole('button', { name: '＋ 新建', exact: true }).click()
  await input.waitFor()
  const freshId = await page.locator('.session-item.selected').getAttribute('data-session-id')
  const newTitle = `新作品 ${marker}`
  await input.fill(newTitle)
  await input.press('Enter')
  await page.waitForFunction((value) => document.querySelector('.canvas-title')?.textContent === value, newTitle)
  const fresh = await request(`/api/studio/sessions/${freshId}`)
  if (fresh.id === first.id || fresh.title !== newTitle || fresh.document.elements.length !== 0 || fresh.messages.length !== 0) throw new Error('Creating a named session changed another session')
  if (await viewer.locator('.canvas-title').textContent() !== title) throw new Error('Naming a new session changed another window title')
  await page.setViewportSize({ width: 390, height: 700 })
  const mobile = await measure()
  if (mobile.bodyHeight > mobile.height + 1 || mobile.bodyWidth > mobile.width + 1 || mobile.composer.bottom > mobile.height + 1) throw new Error(`Mobile page exceeds viewport: ${JSON.stringify(mobile)}`)
  await page.goto('/settings', { waitUntil: 'networkidle' })
  await page.getByRole('button', { name: '保存配置', exact: true }).scrollIntoViewIfNeeded()
  if (!(await page.getByRole('button', { name: '保存配置', exact: true }).isVisible())) throw new Error('Settings cannot scroll to save')
  if (errors.length) throw new Error(errors.join('\n'))
  console.log(JSON.stringify({ bounded_page: true, session_list_scroll: true, conversation_scroll: true,
    rename_persistence: true, rename_cancel_and_validation: true, named_new_session: true, mobile_layout: true,
    settings_scroll: true, rename_window_sync: true, viewport_height: before.height, page_height: before.bodyHeight }))
} finally {
  await context.close()
  await browser.close()
}
