import { chromium } from 'playwright'
import { copyFile, mkdir, writeFile } from 'node:fs/promises'
import { resolve } from 'node:path'

const root = resolve('..')
const videoDirectory = resolve(root, 'runs', 'recording')
const browser = await chromium.launch({
  headless: true,
  ...(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } :
    process.platform === 'win32' ? { channel: 'chrome' } : {}),
})

await mkdir(videoDirectory, { recursive: true })
const context = await browser.newContext({
  viewport: { width: 1440, height: 900 },
  deviceScaleFactor: 1,
  recordVideo: { dir: videoDirectory, size: { width: 1440, height: 900 } },
})
const page = await context.newPage()
const started = Date.now()

async function send(prompt, expected) {
  await page.getByRole('textbox', { name: '绘图指令' }).fill(prompt)
  await page.getByRole('button', { name: '发送 ↑' }).click()
  await page.getByText(expected, { exact: true }).waitFor({ state: 'visible', timeout: 30000 })
  await page.waitForTimeout(450)
}

try {
  await page.goto(process.env.DRAW_AGENT_URL || 'http://127.0.0.1:8000', { waitUntil: 'networkidle' })
  await page.waitForFunction(() => Boolean(localStorage.getItem('draw-agent-session')) &&
    ![...document.querySelectorAll('button')].find((item) => item.textContent.includes('新建'))?.disabled)
  const previousSession = await page.evaluate(() => localStorage.getItem('draw-agent-session'))
  await page.getByRole('button', { name: '＋ 新建' }).click()
  await page.waitForFunction((previous) => localStorage.getItem('draw-agent-session') !== previous, previousSession)
  await page.waitForFunction(() => document.querySelector('.revision')?.textContent.includes('版本 0'))
  await send('画一个蓝色圆形，名字叫主圆。', '已画好主圆。')
  await send('把它向右移动 80 像素。', '已移动 80 像素。')
  await send('用画笔画一张笑脸，展示作画过程', '已用画笔依次画出轮廓、眼睛和微笑。')
  await page.waitForTimeout(1200)
  const output = resolve(root, 'runs', 'draw-agent-demo.webm')
  const video = page.video()
  await context.close()
  await copyFile(await video.path(), output)
  const report = { output, duration_ms: Date.now() - started, prompts: 3 }
  await writeFile(resolve(root, 'runs', 'draw-agent-demo.json'), JSON.stringify(report, null, 2), 'utf8')
  console.log(JSON.stringify(report))
} finally {
  if (page.context().pages().length) await context.close()
  await browser.close()
}
