import { chromium } from 'playwright'

const browser = await chromium.launch({
  headless: true,
  ...(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } :
    process.platform === 'win32' ? { channel: 'chrome' } : {}),
})
const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, acceptDownloads: true })
const baseUrl = process.env.DRAW_AGENT_URL || 'http://127.0.0.1:8000'

async function scene() {
  return page.evaluate(async () => {
    const sessionId = localStorage.getItem('draw-agent-session')
    return (await (await fetch(`/api/studio/sessions/${sessionId}`)).json()).document
  })
}

async function waitForCount(count) {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    const current = await scene()
    if (current.elements.filter((item) => !item.isDeleted).length === count) return
    await page.waitForTimeout(100)
  }
  throw new Error(`Expected ${count} elements; received ${(await scene()).elements.length}`)
}

try {
  await page.goto(baseUrl, { waitUntil: 'networkidle' })
  await page.waitForFunction(() => Boolean(localStorage.getItem('draw-agent-session')) &&
    ![...document.querySelectorAll('button')].find((item) => item.textContent.includes('新建'))?.disabled)
  const previousSession = await page.evaluate(() => localStorage.getItem('draw-agent-session'))
  await page.getByRole('button', { name: '＋ 新建' }).click()
  await page.waitForFunction((previous) => localStorage.getItem('draw-agent-session') !== previous, previousSession)
  await page.waitForFunction(() => document.querySelector('.revision')?.textContent.includes('版本 0'))
  await page.locator('label[title^="Rectangle"]').click()
  const box = await page.locator('.excalidraw__canvas.interactive').boundingBox()
  await page.mouse.move(box.x + 280, box.y + 250)
  await page.mouse.down()
  await page.mouse.move(box.x + 420, box.y + 340, { steps: 8 })
  await page.mouse.up()
  await waitForCount(1)
  const drawn = await scene()
  if (drawn.elements[0].type !== 'rectangle') throw new Error('Manual rectangle was not saved')

  await page.getByRole('button', { name: '↶ 撤销' }).click()
  await waitForCount(0)
  await page.getByRole('button', { name: '↷ 重做' }).click()
  await waitForCount(1)
  await page.reload({ waitUntil: 'networkidle' })
  await waitForCount(1)

  const downloadPromise = page.waitForEvent('download')
  await page.getByRole('button', { name: '导出作品' }).click()
  await page.getByRole('button', { name: '下载文件' }).click()
  const download = await downloadPromise
  if (!download.suggestedFilename().endsWith('.png')) throw new Error('PNG export failed')
  console.log(JSON.stringify({ manual_draw: true, undo: true, redo: true,
    reload: true, png_export: true, document_version: (await scene()).version }))
} finally {
  await browser.close()
}
