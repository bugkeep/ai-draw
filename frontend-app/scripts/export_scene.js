import { chromium } from 'playwright'
import { mkdir } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'

const baseUrl = process.env.DRAW_AGENT_URL || 'http://127.0.0.1:8000'
const sessionId = process.env.DRAW_AGENT_SESSION
const output = resolve(process.env.DRAW_AGENT_EXPORT || '../runs/portrait.png')
if (!sessionId) throw new Error('Set DRAW_AGENT_SESSION to an existing canvas session')
await mkdir(dirname(output), { recursive: true })
const browser = await chromium.launch({ headless: true,
  ...(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : process.platform === 'win32' ? { channel: 'chrome' } : {}),
})
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, acceptDownloads: true })
const errors = []
page.on('pageerror', (error) => errors.push(error.message))
try {
  await page.goto(baseUrl, { waitUntil: 'networkidle' })
  await page.locator(`[data-session-id="${sessionId}"]`).click()
  await page.waitForFunction((id) => localStorage.getItem('draw-agent-session') === id &&
    !document.querySelector('.activity'), sessionId)
  const downloadPromise = page.waitForEvent('download')
  await page.getByRole('button', { name: '导出作品' }).click()
  await page.getByRole('button', { name: '下载文件' }).click()
  const download = await downloadPromise
  if (await download.failure()) throw new Error(await download.failure())
  await download.saveAs(output)
  if (errors.length) throw new Error(errors.join('\n'))
  console.log(JSON.stringify({ session_id: sessionId, output, png_export: true, browser_errors: errors }))
} finally {
  await browser.close()
}
