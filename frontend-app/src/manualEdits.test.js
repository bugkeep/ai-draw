import test from 'node:test'
import assert from 'node:assert/strict'
import { ManualEdits } from './manualEdits.js'
import { StrokeScene } from './strokeScene.js'

const shape = (id, x = 0) => ({ id, type: 'rectangle', x, y: 10, width: 20, height: 30, strokeColor: '#000000', isDeleted: false })
const document = { id: 'canvas', version: 0, elements: [shape('a'), shape('b', 80)], selection: [] }
const journal = () => { let id = 0; return new ManualEdits(() => `gesture-${++id}`) }

test('a pending move survives remote additions and an independent color edit without claiming those changes', () => {
  const edits = journal(), original = structuredClone(document)
  const local = [{ ...original.elements[0], x: 25 }, original.elements[1]]
  edits.capture(original, local, ['a'])
  const remote = { ...original, version: 1, elements: [{ ...original.elements[0], strokeColor: '#ff0000' },
    original.elements[1], shape('remote', 150)] }
  const display = edits.project(remote)
  assert.equal(display.elements[0].x, 25)
  assert.equal(display.elements[0].strokeColor, '#ff0000')
  assert.equal(display.elements[2].id, 'remote')
  const further = display.elements.map((item) => item.id === 'a' ? { ...item, y: 45 } : item)
  edits.capture(display, further, ['a'])
  const request = edits.start('browser')
  assert.deepEqual(request.changes[0].fields.sort(), ['x', 'y'])
  assert.equal(request.changes.length, 1)
  assert.deepEqual(document, original)
})

test('later input remains pending across an in-flight acknowledgement and response-loss retries use the same identity', () => {
  const edits = journal()
  const first = [{ ...document.elements[0], x: 20 }, document.elements[1]]
  edits.capture(document, first, ['a'])
  const inflight = structuredClone(edits.start('browser'))
  const display = edits.project(document)
  edits.capture(display, [{ ...first[0], x: 40 }, first[1]], ['a'])
  assert.deepEqual(edits.start('browser'), inflight)
  const saved = { ...document, version: 1, elements: first }
  edits.acknowledge(inflight.mutation_id)
  assert.equal(edits.project(saved).elements[0].x, 40)
  const next = edits.start('browser')
  assert.notEqual(next.mutation_id, inflight.mutation_id)
  assert.equal(next.changes[0].before.x, 20)
  assert.equal(next.changes[0].after.x, 40)
})

test('a new style change captures its current expectation while retaining the earlier geometry intent', () => {
  const edits = journal()
  edits.capture(document, [{ ...document.elements[0], x: 15 }, document.elements[1]], ['a'])
  const remote = { ...document, elements: [{ ...document.elements[0], strokeColor: '#ff0000' }, document.elements[1]] }
  const display = edits.project(remote)
  edits.capture(display, [{ ...display.elements[0], strokeColor: '#0000ff' }, display.elements[1]], ['a'])
  const change = edits.start('browser').changes[0]
  assert.equal(change.before.x, 0)
  assert.equal(change.before.strokeColor, '#ff0000')
  assert.equal(change.after.strokeColor, '#0000ff')
})

test('conflicts retain local geometry; explicit resolution rebases only intended changes and preserves remote additions', () => {
  const edits = journal()
  edits.capture(document, [{ ...document.elements[0], x: 15 }, document.elements[1]], ['a'])
  edits.start('browser')
  edits.conflicted = true
  const remote = { ...document, elements: [{ ...document.elements[0], x: 70, strokeColor: '#ff0000' },
    document.elements[1], shape('remote')] }
  assert.throws(() => edits.start('browser'), /本地修改/)
  assert.equal(edits.project(remote).elements[0].x, 15)
  edits.keepLocal(remote)
  const change = edits.start('browser').changes[0]
  assert.equal(change.before.x, 70)
  assert.equal(change.after.x, 15)
  assert.equal(change.after.strokeColor, '#ff0000')
  edits.reset()
  assert.equal(edits.project(remote), remote)
})

test('new object placement and deletion retain unrelated remote objects and do not change canvas identity', () => {
  const edits = journal(), newShape = shape('new', 45)
  edits.capture(document, [document.elements[0], newShape, document.elements[1]], ['new'])
  const remote = { ...document, elements: [...document.elements, shape('remote', 150)] }
  assert.deepEqual(edits.project(remote).elements.map((item) => item.id), ['a', 'new', 'b', 'remote'])
  assert.throws(() => edits.capture({ ...document, id: 'other' }, [newShape], []), /another canvas/)
  const current = edits.project(remote)
  edits.capture(current, current.elements.filter((item) => item.id !== 'new'), [])
  assert.equal(edits.start('browser').changes.length, 0)
})

test('stroke frames clone the base once per revision, isolate canonical objects and refresh an interrupted frame', () => {
  let fullClones = 0, strokeClones = 0
  const cache = new StrokeScene((value) => { if (Array.isArray(value)) fullClones += 1; else strokeClones += 1; return structuredClone(value) })
  for (let index = 0; index < 60; index += 1) {
    const frame = cache.frame(document.elements, { id: 'preview', points: [[0, 0], [index, 5]] })
    assert.equal(frame.length, document.elements.length + 1)
    frame[0].index = 'editor-metadata'
  }
  assert.equal(fullClones, 1)
  assert.equal(strokeClones, 60)
  assert.equal(document.elements[0].index, undefined)
  const restored = cache.base(document.elements, true)
  assert.equal(restored.length, 2)
  assert.equal(restored[0].index, undefined)
  cache.reset()
  cache.base(document.elements)
  assert.equal(fullClones, 3)
})
