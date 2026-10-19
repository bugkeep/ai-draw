import test from 'node:test'
import assert from 'node:assert/strict'
import { applyScenePatch, replayScenePatches, sceneSnapshotSignature } from './scenePatch.js'

const base = { id: 'a', title: '画布', version: 2, updated_at: 1,
  elements: [{ id: 'first', x: 1, points: [[0, 0], [2, 3]] }, { id: 'second', x: 9 }], selection: [] }
const patch = (values = {}) => ({ id: 'a', title: '画布', base_version: 2, version: 3, updated_at: 2,
  upserts: [], removed_ids: [], selection: [], ...values })
const event = (values) => ({ type: 'scene_patch', payload: { patch: patch(values) } })

test('edits preserve unchanged geometry, append order and selected IDs without mutating the snapshot', () => {
  const first = { ...base.elements[0], x: 10 }
  const result = applyScenePatch(base, patch({ upserts: [first, { id: 'third', x: 5 }], selection: ['third'] }))
  assert.deepEqual(result.elements.map((element) => element.id), ['first', 'second', 'third'])
  assert.equal(result.elements[0], first)
  assert.equal(result.elements[1], base.elements[1])
  assert.deepEqual(result.selection, ['third'])
  assert.equal(base.elements[0].x, 1)
})

test('removal, layer changes and empty-canvas undo retain the exact supplied order', () => {
  const reordered = applyScenePatch(base, patch({ upserts: [{ id: 'third' }], removed_ids: ['first'],
    order: ['third', 'second'], selection: ['second'] }))
  assert.deepEqual(reordered.elements.map((element) => element.id), ['third', 'second'])
  const empty = applyScenePatch(reordered, patch({ base_version: 3, version: 4,
    removed_ids: ['third', 'second'], order: [] }))
  assert.deepEqual(empty.elements, [])
})

test('old frames cannot overwrite a newer scene; gaps and malformed patches request a snapshot', () => {
  assert.equal(applyScenePatch(base, patch({ version: 1 })), base)
  for (const values of [{ id: 'other' }, { base_version: 1 }, { version: 5 }, { selection: ['missing'] },
    { order: ['second', 'second'] }, { upserts: [{ id: 'new' }, { id: 'new' }] },
    { upserts: [{ id: 'first' }], removed_ids: ['first'] }]) {
    assert.equal(applyScenePatch(base, patch(values)), null)
  }
})

test('recovery replays buffered newer frames, discards duplicates and keeps unresolved gaps', () => {
  const frames = [event({ base_version: 3, version: 4, upserts: [{ id: 'new', x: 4 }] }),
    event({ upserts: [{ id: 'new', x: 3 }] }), event({ version: 2 }), event({ id: 'other' }),
    event({ base_version: 5, version: 6 })]
  const recovered = replayScenePatches(base, frames)
  assert.equal(recovered.document.version, 4)
  assert.equal(recovered.document.elements.at(-1).x, 4)
  assert.equal(recovered.pending.length, 1)
  const snapshot = { ...recovered.document, version: 6 }
  assert.deepEqual(replayScenePatches(snapshot, recovered.pending), { document: snapshot, pending: [] })
})

test('editor normalization is not mistaken for a drawing edit, while geometry and appearance remain significant', () => {
  const normalized = base.elements.map((element) => ({ ...element, version: 42, versionNonce: 567,
    updated: 999, index: 'a0', boundElements: [] }))
  assert.equal(sceneSnapshotSignature(base.elements, []), sceneSnapshotSignature(normalized, []))
  for (const changes of [{ x: 12 }, { seed: 7 }, { strokeColor: '#ff0000' }, { isDeleted: true }]) {
    assert.notEqual(sceneSnapshotSignature(base.elements, []),
      sceneSnapshotSignature([{ ...normalized[0], ...changes }, normalized[1]], []))
  }
})
