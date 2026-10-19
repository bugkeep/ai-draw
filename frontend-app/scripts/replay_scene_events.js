import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { applyScenePatch, sceneSnapshotSignature } from '../src/scenePatch.js'

const { initial, events, expected } = JSON.parse(await readFile(process.argv[2], 'utf8'))
let current = initial
for (const event of events) {
  current = applyScenePatch(current, event.payload.patch)
  assert.ok(current, `Scene revision gap at event ${event.seq}`)
}
assert.equal(current.id, expected.id)
assert.equal(current.version, expected.version)
assert.equal(sceneSnapshotSignature(current.elements, current.selection),
  sceneSnapshotSignature(expected.elements, expected.selection))
console.log(JSON.stringify({ all_revisions_replayed: true, final_native_content_and_layer_order: true,
  final_selection: true, browser_patch_reducer_used: true }))
