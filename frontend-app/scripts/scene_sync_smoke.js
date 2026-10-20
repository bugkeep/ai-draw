import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { chromium } from 'playwright'

const baseURL = process.env.DRAW_AGENT_URL || 'http://127.0.0.1:8000'
const browser = await chromium.launch({ headless: true,
  ...(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } :
    process.platform === 'win32' ? { channel: 'chrome' } : {}),
})
const context = await browser.newContext({ baseURL, viewport: { width: 1440, height: 900 }, acceptDownloads: true })
const errors = []
const page = await context.newPage()
const viewer = await context.newPage()
for (const target of [page, viewer]) target.on('pageerror', (error) => errors.push(error.message))
let connectionCount = 0
let activeRoute
let dropNext = false
let reorderNext = false
let held
let dropped = 0
let patchBytes = 0
let patchCount = 0
let fullBytes = 0
let sessionReads = 0
let cancelOnStroke = false
let cancelledStrokeTask
page.on('request', (request) => {
  if (request.method() === 'GET' && /\/api\/studio\/sessions\/[^/]+$/.test(request.url())) sessionReads += 1
})

async function request(path, data, method = 'POST') {
  const response = data === undefined ? await context.request.get(path) :
    await context.request.fetch(path, { method, data })
  assert.ok(response.ok(), `${path}: HTTP ${response.status()} ${await response.text()}`)
  return response.json()
}

async function revision(target, version) {
  await target.waitForFunction((value) => document.querySelector('.revision')?.textContent.startsWith(`版本 ${value} ·`), version)
}

async function exported(target) {
  await target.getByRole('button', { name: '导出作品', exact: true }).click()
  const dialog = target.getByRole('dialog', { name: '导出作品' })
  await dialog.getByLabel('文件格式').selectOption('excalidraw')
  const downloading = target.waitForEvent('download')
  await dialog.getByRole('button', { name: '下载文件' }).click()
  const download = await downloading
  return JSON.parse(await readFile(await download.path(), 'utf8'))
}

function geometry(elements) {
  return elements.filter((element) => !element.isDeleted).map((element) => ({
    id: element.id, type: element.type, x: element.x, y: element.y, width: element.width,
    height: element.height, points: element.points, strokeColor: element.strokeColor,
  }))
}

try {
  const first = await request('/api/studio/sessions', { title: '连续同步与重连验收' })
  const other = await request('/api/studio/sessions', { title: '独立画布' })
  const task = await request(`/api/studio/sessions/${first.id}/messages`, { message: '画一个蓝色圆形' })
  let initialFinished = false
  for (let attempt = 0; attempt < 100; attempt += 1) {
    const state = await request(`/api/studio/tasks/${task.id}`)
    if (state.status === 'completed') { initialFinished = true; break }
    if (['failed', 'partial', 'cancelled'].includes(state.status)) throw new Error(state.error || state.status)
    await new Promise((resolve) => setTimeout(resolve, 50))
  }
  assert.ok(initialFinished, 'Initial deterministic circle task did not finish')
  let document = (await request(`/api/studio/sessions/${first.id}`)).document
  const seed = document.elements.find((element) => !element.isDeleted)
  assert.ok(seed)
  const elements = process.env.DRAW_AGENT_SYNC_SOURCE ?
    JSON.parse(await readFile(process.env.DRAW_AGENT_SYNC_SOURCE, 'utf8')).elements :
    Array.from({ length: 48 }, (_, index) => ({ ...seed, id: `sync-${index}`,
      x: (index % 12) * 45, y: Math.floor(index / 12) * 45, width: 28, height: 28 }))
  document = await request(`/api/studio/documents/${document.id}/scene`, {
    version: document.version, elements, selection: [], client_id: 'test-seed',
  }, 'PUT')
  await context.addInitScript((id) => localStorage.setItem('draw-agent-session', id), first.id)
  await page.routeWebSocket(/\/api\/studio\/documents\/[^/]+\/events/, (ws) => {
    assert.ok(ws.url().includes('patches=true'))
    const server = ws.connectToServer()
    activeRoute = { ws, server }
    connectionCount += 1
    server.onMessage((message) => {
      const event = JSON.parse(message.toString())
      if (event.type === 'stroke_progress' && cancelOnStroke) {
        cancelOnStroke = false
        ws.send(message)
        cancelledStrokeTask = request(`/api/studio/tasks/${event.payload.task_id}/cancel`, {})
        return
      }
      if (event.type === 'scene_patch') {
        patchCount += 1
        patchBytes += Buffer.byteLength(message)
        if (dropNext) { dropNext = false; dropped += 1; return }
        if (reorderNext) { held = message; reorderNext = false; return }
        if (held) {
          ws.send(message)
          ws.send(held)
          held = undefined
          return
        }
      }
      ws.send(message)
    })
  })
  await Promise.all([page.goto('/', { waitUntil: 'networkidle' }), viewer.goto('/', { waitUntil: 'networkidle' })])
  await Promise.all([revision(page, document.version), revision(viewer, document.version)])

  async function edit() {
    const next = document.elements.map((element, index) => index ? element : { ...element, x: element.x + 2 })
    document = await request(`/api/studio/documents/${document.id}/scene`, {
      version: document.version, elements: next, selection: [next[0].id], client_id: 'external-editor',
    }, 'PUT')
    fullBytes += Buffer.byteLength(JSON.stringify(document))
  }
  for (let index = 0; index < 30; index += 1) await edit()
  await Promise.all([revision(page, document.version), revision(viewer, document.version)])
  const settled = (await request(`/api/studio/sessions/${first.id}`)).document
  assert.equal(settled.version, document.version, 'Applying remote updates must not save an extra local change')
  assert.deepEqual(settled.selection, [elements[0].id], 'The editor must preserve the remote selection')
  assert.deepEqual(geometry((await exported(page)).elements), geometry(document.elements))
  assert.deepEqual(geometry((await exported(viewer)).elements), geometry(document.elements))

  const readsBeforeGap = sessionReads
  dropNext = true
  await edit()
  await edit()
  await revision(page, document.version)
  assert.ok(dropped === 1 && sessionReads > readsBeforeGap, 'Missing revision did not trigger snapshot recovery')
  assert.deepEqual(geometry((await exported(page)).elements), geometry(document.elements))

  reorderNext = true
  await edit()
  await edit()
  await revision(page, document.version)
  assert.deepEqual(geometry((await exported(page)).elements), geometry(document.elements))

  const previousConnections = connectionCount
  await activeRoute.ws.close({ code: 1000 })
  await activeRoute.server.close({ code: 1000 })
  for (let index = 0; index < 4; index += 1) await edit()
  await revision(page, document.version)
  assert.ok(connectionCount > previousConnections, 'Editor did not reconnect')
  assert.deepEqual(geometry((await exported(page)).elements), geometry(document.elements))

  await page.locator(`[data-session-id="${other.id}"]`).click()
  await revision(page, 0)
  await edit()
  await revision(viewer, document.version)
  assert.equal((await exported(page)).elements.length, 0, 'A previous canvas event crossed into another session')
  cancelOnStroke = true
  const interrupted = await request(`/api/studio/sessions/${other.id}/messages`, { message: '用画笔画一张笑脸' })
  let cancelled = false
  for (let attempt = 0; attempt < 100; attempt += 1) {
    const state = await request(`/api/studio/tasks/${interrupted.id}`)
    if (state.status === 'cancelled') { cancelled = true; break }
    await new Promise((resolve) => setTimeout(resolve, 30))
  }
  assert.ok(cancelled && cancelledStrokeTask, 'Task was not cancelled during its actual stroke animation')
  await cancelledStrokeTask
  await page.waitForFunction(() => !document.querySelector('.activity'))
  const savedAfterCancel = (await request(`/api/studio/sessions/${other.id}`)).document
  assert.deepEqual(geometry((await exported(page)).elements), geometry(savedAfterCancel.elements),
    'An interrupted transient stroke remained visible after task cancellation')
  assert.equal(errors.length, 0, errors.join('\n'))
  assert.ok(patchCount >= 38 && patchBytes < fullBytes / 8, 'Scene events still repeat the full canvas')
  console.log(JSON.stringify({ continuous_updates: true, native_export_matches_both_windows: true,
    missing_revision_resync: true, reordered_revision_resync: true, reconnect_replay: true,
    session_isolation: true, cancelled_stroke_restores_saved_scene: true,
    no_browser_errors: true, patch_count: patchCount, patch_bytes: patchBytes,
    equivalent_full_document_bytes: fullBytes, native_object_count: document.elements.filter((element) => !element.isDeleted).length,
    connection_count: connectionCount }))
} catch (error) {
  console.error(JSON.stringify({ current_revision: await page.locator('.revision').textContent().catch(() => ''),
    viewer_revision: await viewer.locator('.revision').textContent().catch(() => ''), session_reads: sessionReads,
    patch_count: patchCount, dropped, connection_count: connectionCount, browser_errors: errors }))
  throw error
} finally {
  await context.close()
  await browser.close()
}
