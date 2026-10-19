import { createServer } from 'node:http'
import { createInterface } from 'node:readline'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright'
import sirv from 'sirv'

const root = fileURLToPath(new URL('../../frontend-dist/', import.meta.url))
const server = createServer(sirv(root, { single: false }))
await new Promise((resolve, reject) => {
  server.once('error', reject)
  server.listen(0, '127.0.0.1', resolve)
})
let browser
try {
  browser = await chromium.launch({ headless: true,
    ...(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } :
      process.platform === 'win32' ? { channel: 'chrome' } : {}) })
  const page = await browser.newPage()
  await page.goto(`http://127.0.0.1:${server.address().port}/render.html`)
  await page.waitForFunction(() => typeof window.renderDrawing === 'function')
  process.stdout.write(`${JSON.stringify({ ready: true })}\n`)
  const requests = createInterface({ input: process.stdin, crlfDelay: Infinity })
  for await (const line of requests) {
    const request = JSON.parse(line)
    try {
      const result = await page.evaluate((drawing) => window.renderDrawing(drawing), request.drawing)
      process.stdout.write(`${JSON.stringify({ id: request.id, result })}\n`)
    } catch (error) {
      process.stdout.write(`${JSON.stringify({ id: request.id, error: error.message })}\n`)
    }
  }
} finally {
  if (browser) await browser.close()
  await new Promise((resolve) => server.close(resolve))
}
