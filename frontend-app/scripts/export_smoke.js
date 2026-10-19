import { chromium } from 'playwright'
import { readFile, mkdir } from 'node:fs/promises'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const baseUrl = process.env.DRAW_AGENT_URL || 'http://127.0.0.1:8000'
const directory = fileURLToPath(new URL('../../runs/export-check/', import.meta.url))
await mkdir(directory, { recursive: true })
const browser = await chromium.launch({ headless: true,
  ...(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : process.platform === 'win32' ? { channel: 'chrome' } : {}),
})
const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, acceptDownloads: true })
const errors = []
page.on('pageerror', (error) => errors.push(error.message))
try {
  await page.goto(baseUrl, { waitUntil: 'networkidle' })
  await page.waitForSelector('.session-item.selected')
  const response = await page.request.post(`${baseUrl}/api/studio/sessions`, { data: { title: '导出格式检查' } })
  if (!response.ok()) throw new Error('Could not create export canvas')
  const session = await response.json()
  await page.reload({ waitUntil: 'networkidle' })
  await page.locator(`[data-session-id="${session.id}"]`).click()
  await page.getByRole('textbox', { name: '绘图指令' }).fill('用画笔画一张笑脸')
  await page.getByRole('button', { name: '发送 ↑' }).click()
  await page.waitForFunction(() => !document.querySelector('.activity') && document.querySelector('.revision')?.textContent.includes('4 个对象'))
  const checks = {}
  const dimensions = {}
  for (const [format, scale] of [['png', 1], ['png', 2], ['png', 4], ['jpg', 1], ['webp', 1], ['svg', 1], ['excalidraw', 1]]) {
    await page.getByRole('button', { name: '导出作品', exact: true }).click()
    const dialog = page.getByRole('dialog', { name: '导出作品' })
    await dialog.getByLabel('文件名称').fill('绘图/格式验收')
    await dialog.getByLabel('文件格式').selectOption(format)
    if (['png', 'jpg', 'webp'].includes(format)) await dialog.getByLabel('图片清晰度').selectOption(String(scale))
    if (format === 'png') await dialog.getByLabel('透明背景').check()
    if (format === 'jpg' && !(await dialog.getByLabel('透明背景').isDisabled())) throw new Error('JPG exposed transparency')
    const downloading = page.waitForEvent('download')
    await dialog.getByRole('button', { name: '下载文件' }).click()
    const download = await downloading
    if (await download.failure()) throw new Error(await download.failure())
    const filename = download.suggestedFilename()
    if (filename !== `绘图_格式验收.${format}`) throw new Error(`Incorrect export name ${filename}`)
    const output = resolve(directory, `${scale}-${filename}`)
    await download.saveAs(output)
    const bytes = await readFile(output)
    if (format === 'png' && !bytes.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]))) throw new Error('Invalid PNG')
    if (format === 'jpg' && !(bytes[0] === 255 && bytes[1] === 216)) throw new Error('Invalid JPG')
    if (format === 'webp' && !(bytes.subarray(0, 4).toString() === 'RIFF' && bytes.subarray(8, 12).toString() === 'WEBP')) throw new Error('Invalid WebP')
    if (['png', 'jpg', 'webp'].includes(format)) {
      dimensions[`${format}-${scale}`] = await page.evaluate(async (array) => {
        const bitmap = await createImageBitmap(new Blob([new Uint8Array(array)]))
        const canvas = document.createElement('canvas')
        canvas.width = bitmap.width
        canvas.height = bitmap.height
        const context = canvas.getContext('2d')
        context.drawImage(bitmap, 0, 0)
        const result = { width: bitmap.width, height: bitmap.height, cornerAlpha: context.getImageData(0, 0, 1, 1).data[3] }
        bitmap.close()
        return result
      }, [...bytes])
    }
    if (format === 'svg') {
      const valid = await page.evaluate((source) => {
        const svg = new DOMParser().parseFromString(source, 'image/svg+xml')
        return svg.documentElement.localName === 'svg' && !svg.querySelector('parsererror') && Boolean(svg.querySelector('path'))
      }, bytes.toString('utf8'))
      if (!valid) throw new Error('Invalid SVG')
    }
    if (format === 'excalidraw') {
      const scene = JSON.parse(bytes.toString('utf8'))
      if (scene.type !== 'excalidraw' || scene.elements.length !== 4 || scene.elements.some((item) => item.type !== 'freedraw')) throw new Error('Native drawing data was not retained')
    }
    checks[`${format}-${scale}`] = true
  }
  for (const scale of [2, 4]) {
    if (dimensions[`png-${scale}`].width !== dimensions['png-1'].width * scale || dimensions[`png-${scale}`].height !== dimensions['png-1'].height * scale) throw new Error('Export scale was not applied')
  }
  if (dimensions['png-1'].cornerAlpha !== 0 || dimensions['jpg-1'].cornerAlpha !== 255) throw new Error('Export background was not applied')
  await page.setViewportSize({ width: 360, height: 740 })
  const exportButton = page.getByRole('button', { name: '导出作品', exact: true })
  const buttonBox = await exportButton.boundingBox()
  if (!buttonBox || buttonBox.x < 0 || buttonBox.x + buttonBox.width > 360) throw new Error('Export button is unavailable on a narrow screen')
  await exportButton.click()
  const dialogBox = await page.getByRole('dialog', { name: '导出作品' }).boundingBox()
  if (!dialogBox || dialogBox.x < 0 || dialogBox.x + dialogBox.width > 360 || dialogBox.y < 0 || dialogBox.y + dialogBox.height > 740) throw new Error('Export dialog exceeds the narrow viewport')
  await page.getByRole('dialog', { name: '导出作品' }).press('Escape')
  if (await page.getByRole('dialog').count()) throw new Error('Export cancellation did not close the dialog')
  if (errors.length) throw new Error(errors.join('\n'))
  console.log(JSON.stringify({ formats: checks, resolution_scale: true, background: true, safe_filename: true, narrow_viewport: true, cancel: true, no_browser_errors: true }))
} finally {
  await browser.close()
}
