import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { chromium } from 'playwright'
import { sceneSnapshotSignature } from '../src/scenePatch.js'

const baseURL = process.env.DRAW_AGENT_URL || 'http://127.0.0.1:8000'
const browser = await chromium.launch({ headless: true,
  ...(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : process.platform === 'win32' ? { channel: 'chrome' } : {}) })
const context = await browser.newContext({ baseURL, viewport: { width: 1440, height: 900 }, acceptDownloads: true })
await context.addInitScript(() => {
  const original = window.structuredClone.bind(window)
  window.cloneMetrics = { full: 0 }
  window.structuredClone = (value, ...options) => {
    if (Array.isArray(value) && value.length >= 300) window.cloneMetrics.full += 1
    return original(value, ...options)
  }
})
const page = await context.newPage()
const errors = []
page.on('pageerror', (error) => errors.push(error.message))
let hold = false, loseResponse = false, held, release
let frames = 0, patches = 0
page.on('websocket', (socket) => socket.on('framereceived', ({ payload }) => {
  const event = JSON.parse(payload.toString())
  if (event.type === 'stroke_progress') frames += 1
  if (event.type === 'scene_patch') patches += 1
}))

async function request(path, data, method = 'POST') {
  const response = data === undefined ? await context.request.get(path) : await context.request.fetch(path, { method, data })
  assert.ok(response.ok(), `${path}: HTTP ${response.status()} ${await response.text()}`)
  return response.json()
}
async function eventually(read, valid, label) {
  for (let attempt = 0; attempt < 200; attempt += 1) {
    const value = await read()
    if (valid(value)) return value
    await new Promise((resolve) => setTimeout(resolve, 50))
  }
  throw new Error(label)
}
async function task(session, message) {
  const started = await request(`/api/studio/sessions/${session.id}/messages`, { message })
  const result = await eventually(() => request(`/api/studio/tasks/${started.id}`),
    (value) => ['completed', 'partial', 'failed', 'cancelled'].includes(value.status), 'Actual task did not settle')
  assert.equal(result.status, 'completed', result.error)
  return result
}
async function saved(session) { return (await request(`/api/studio/sessions/${session.id}`)).document }
async function exported() {
  await page.getByRole('button', { name: '导出作品' }).click()
  await page.getByLabel('文件格式').selectOption('excalidraw')
  const download = page.waitForEvent('download')
  await page.getByRole('button', { name: '下载文件' }).click()
  return JSON.parse(await readFile(await (await download).path(), 'utf8'))
}
async function open(session) {
  await page.goto('/')
  await page.evaluate((id) => localStorage.setItem('draw-agent-session', id), session.id)
  await page.reload({ waitUntil: 'networkidle' })
  await page.waitForFunction(() => !document.querySelector('.backend-notice'))
  await page.locator('.excalidraw__canvas.interactive').waitFor()
  await page.waitForTimeout(350)
}
async function rectangle() {
  const session = await request('/api/studio/sessions', { title: '真实手动并发编辑' })
  await open(session)
  await page.locator('label[title^="Rectangle"]').click()
  const box = await page.locator('.excalidraw__canvas.interactive').boundingBox()
  await page.mouse.move(box.x + 280, box.y + 250)
  await page.mouse.down()
  await page.mouse.move(box.x + 420, box.y + 340, { steps: 8 })
  await page.mouse.up()
  await eventually(() => saved(session), (value) => value.elements.length === 1, 'Manual rectangle was not saved')
  await page.waitForTimeout(250)
  return { session, box, before: await saved(session) }
}

try {
  await page.route('**/api/studio/documents/*/scene', async (route) => {
    if (route.request().method() !== 'PATCH') return route.continue()
    const body = route.request().postDataJSON()
    if (loseResponse && body.changes.some((change) => change.before === null)) {
      loseResponse = false
      await route.fetch() // Production backend actually commits, then its response is deliberately lost.
      return route.abort('failed')
    }
    if (hold && body.changes.some((change) => change.fields.some((field) => ['x', 'y'].includes(field)))) {
      hold = false
      held = body
      await new Promise((resolve) => { release = resolve })
    }
    await route.continue()
  })

  const cases = {}
  for (const choice of ['merge', 'keep', 'server', 'delete']) {
    const { session, box, before } = await rectangle()
    const target = before.elements[0]
    held = undefined; release = undefined; hold = true
    await page.keyboard.press('v')
    await page.mouse.move(box.x + 350, box.y + 295)
    await page.mouse.down()
    await page.mouse.move(box.x + 395, box.y + 315, { steps: 5 })
    await page.mouse.up()
    await eventually(() => Promise.resolve(held), Boolean, 'Manual drag request was not captured')
    const local = held.changes.find((change) => change.id === target.id).after
    assert.notEqual(local.x, target.x)
    await task(session, choice === 'merge' ? '改成红色' : '向右移动60像素')
    const remote = await saved(session)
    await page.waitForFunction((version) => document.querySelector('.revision')?.textContent.startsWith(`版本 ${version} ·`), remote.version)
    assert.equal((await exported()).elements.find((item) => item.id === target.id).x, local.x,
      'An actual Agent event discarded the held local drag')
    release()
    if (choice === 'merge') {
      const merged = await eventually(() => saved(session), (value) => value.elements[0].x === local.x, 'Disjoint edit did not merge')
      assert.equal(merged.elements[0].strokeColor, '#e03131')
      assert.equal(await page.locator('.save-notice').count(), 0)
    } else {
      await page.getByRole('button', { name: '保留本地修改' }).waitFor()
      assert.equal((await exported()).elements[0].x, local.x, 'Conflict did not retain the local draft')
      if (choice === 'delete') {
        await page.locator(`[data-session-id="${session.id}"]`).locator('..').getByRole('button', { name: '删除画布', exact: true }).click()
        await page.getByRole('dialog', { name: '删除画布会话', exact: true }).getByRole('button', { name: '确认删除', exact: true }).click()
        await page.locator(`[data-session-id="${session.id}"]`).waitFor({ state: 'detached' })
        assert.equal((await context.request.get(`/api/studio/sessions/${session.id}`)).status(), 404)
        cases.delete = true
        continue
      }
      await page.getByRole('button', { name: choice === 'keep' ? '保留本地修改' : '采用最新画布' }).click()
      await eventually(() => saved(session), (value) => value.elements[0].x === (choice === 'keep' ? local.x : remote.elements[0].x), 'Conflict choice not saved')
      await page.waitForFunction(() => !document.querySelector('.save-notice'))
    }
    const final = await saved(session)
    assert.equal(sceneSnapshotSignature((await exported()).elements, []), sceneSnapshotSignature(final.elements, []))
    await page.reload({ waitUntil: 'networkidle' })
    assert.equal(sceneSnapshotSignature((await exported()).elements, []), sceneSnapshotSignature(final.elements, []))
    cases[choice] = true
  }

  const lost = await request('/api/studio/sessions', { title: '响应丢失与幂等' })
  await open(lost)
  loseResponse = true
  await page.locator('label[title^="Rectangle"]').click()
  const box = await page.locator('.excalidraw__canvas.interactive').boundingBox()
  await page.mouse.move(box.x + 280, box.y + 250); await page.mouse.down()
  await page.mouse.move(box.x + 420, box.y + 340, { steps: 5 }); await page.mouse.up()
  await page.getByRole('button', { name: '重试保存' }).waitFor()
  const committed = await saved(lost)
  assert.equal(committed.elements.length, 1)
  await page.getByRole('button', { name: '重试保存' }).click()
  await page.waitForFunction(() => !document.querySelector('.save-notice'))
  const retried = await saved(lost)
  assert.equal(retried.version, committed.version)
  assert.deepEqual(retried.elements, committed.elements)

  // A real gesture immediately after a remote scene update must survive the
  // editor's deferred programmatic callbacks, not be ignored by its guard.
  const recent = await rectangle()
  const original = recent.before.elements[0]
  const shifted = await request(`/api/studio/documents/${recent.before.id}/scene`, {
    version: recent.before.version, elements: [{ ...original, x: original.x + 20, y: original.y + 25 }],
    selection: [original.id], client_id: 'concurrent-real-editor' }, 'PUT')
  await page.waitForFunction((version) => document.querySelector('.revision')?.textContent.startsWith(`版本 ${version} ·`), shifted.version)
  const gestureStarted = Date.now()
  await page.keyboard.press('v')
  await page.mouse.move(recent.box.x + 370, recent.box.y + 320); await page.mouse.down()
  await page.mouse.move(recent.box.x + 405, recent.box.y + 335, { steps: 2 }); await page.mouse.up()
  const gestureMs = Date.now() - gestureStarted
  const captured = await eventually(() => saved(recent.session),
    (value) => value.elements[0].x === original.x + 55, 'Gesture immediately after a remote update was lost')
  assert.equal(captured.elements[0].y, original.y + 40)

  const animation = await request('/api/studio/sessions', { title: '密集画布逐笔展示' })
  await task(animation, '用画笔画一张笑脸')
  let native = await saved(animation)
  const seed = native.elements[0]
  const dense = Array.from({ length: 320 }, (_, index) => ({ ...seed, id: `background-${index}`,
    x: (index % 20) * 100, y: Math.floor(index / 20) * 100 }))
  native = await request(`/api/studio/documents/${native.id}/scene`, {
    version: native.version, elements: dense, selection: [], client_id: 'native-fixture' }, 'PUT')
  await open(animation)
  await page.evaluate(() => { window.cloneMetrics.full = 0 })
  frames = 0; patches = 0
  await task(animation, '用画笔画一张笑脸')
  await page.waitForFunction(() => !document.querySelector('.activity'))
  await page.waitForTimeout(300)
  const clones = await page.evaluate(() => window.cloneMetrics.full)
  assert.ok(frames > 20)
  assert.ok(clones <= patches + 4 && clones < frames / 2, `Full-scene clones ${clones}; patches ${patches}; frames ${frames}`)
  const final = await saved(animation)
  assert.equal(sceneSnapshotSignature((await exported()).elements, []), sceneSnapshotSignature(final.elements, []))
  assert.equal(errors.length, 0, errors.join('\n'))
  console.log(JSON.stringify({ manual_drag_survives_agent_event: true, independent_fields_merge: cases.merge,
    conflict_keep_local: cases.keep, conflict_adopt_latest: cases.server, confirmed_delete_with_local_conflict: cases.delete,
    response_loss_idempotent: true,
    gesture_after_remote_update_saved: true, gesture_duration_ms: gestureMs,
    reload_matches_canonical_native: true, no_browser_errors: true,
    dense_native_objects: final.elements.length, stroke_progress_frames: frames, scene_patches: patches, full_scene_clones: clones,
    scope: 'actual editor gestures, actual production HTTP and deterministic drawing commands; no model response mocks or full-art acceptance' }))
} finally {
  if (release) release()
  await context.close()
  await browser.close()
}
