import { chromium } from 'playwright'

const baseUrl = process.env.DRAW_AGENT_URL || 'http://127.0.0.1:8000'
const sessionId = process.env.DRAW_AGENT_SESSION
if (!sessionId) throw new Error('Set DRAW_AGENT_SESSION to a canvas with actual completed research')
const browser = await chromium.launch({ headless: true,
  ...(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : process.platform === 'win32' ? { channel: 'chrome' } : {}),
})
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
const errors = []
page.on('pageerror', (error) => errors.push(error.message))
try {
  const response = await page.request.get(`${baseUrl}/api/studio/sessions/${sessionId}`)
  if (!response.ok()) throw new Error('Could not read research canvas')
  const session = await response.json()
  const expected = session.messages.flatMap((message) => message.references || [])
  if (!expected.length) throw new Error('No actual verified source records were found')
  await page.goto(baseUrl, { waitUntil: 'networkidle' })
  await page.locator(`[data-session-id="${sessionId}"]`).click()
  async function checkReferences() {
    await page.locator('.message-references a').first().waitFor()
    const links = await page.locator('.message-references a').evaluateAll((items) => items.map((item) => ({ url: item.href, title: item.textContent })))
    if (links.length !== expected.length || !expected.every((source) => links.some((link) => link.url === source.url && link.title === source.title))) throw new Error('Source links do not match verified records')
  }
  await checkReferences()
  await page.reload({ waitUntil: 'networkidle' })
  await checkReferences()
  const created = await page.request.post(`${baseUrl}/api/studio/sessions`, { data: { title: '参考资料归属检查' } })
  if (!created.ok()) throw new Error('Could not create isolated canvas')
  const other = await created.json()
  await page.reload({ waitUntil: 'networkidle' })
  await page.locator(`[data-session-id="${other.id}"]`).click()
  await page.getByRole('textbox', { name: '绘图指令' }).fill('画一个蓝色圆形')
  await page.getByRole('button', { name: '发送 ↑' }).click()
  await page.waitForFunction(() => !document.querySelector('.activity') && document.querySelector('.revision')?.textContent.includes('1 个对象'))
  if (await page.locator('.message-references').count()) throw new Error('References crossed canvas ownership')
  if (errors.length) throw new Error(errors.join('\n'))
  console.log(JSON.stringify({ verified_links: true, reload: true, canvas_isolation: true, source_count: expected.length, no_browser_errors: true }))
} finally {
  await browser.close()
}
