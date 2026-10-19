import { sceneSnapshotSignature } from './scenePatch.js'

const metadata = new Set(['version', 'versionNonce', 'updated', 'index'])
const geometry = new Set(['x', 'y', 'width', 'height', 'angle', 'points', 'pressures', 'lastCommittedPoint'])
const clone = (value) => structuredClone(value)
const same = (first, second) => JSON.stringify(first) === JSON.stringify(second)
const content = (item) => item && ({ ...Object.fromEntries(Object.entries(item).filter(([key]) => !metadata.has(key))),
  boundElements: item.boundElements || [] })
const equal = (first, second) => sceneSnapshotSignature(first ? [first] : [], []) === sceneSnapshotSignature(second ? [second] : [], [])

function fieldsChanged(before, after) {
  const old = content(before), next = content(after)
  return [...new Set([...Object.keys(old), ...Object.keys(next)])]
    .filter((key) => key !== 'id' && !same(old[key], next[key]))
}

function copyField(target, source, field) {
  if (Object.hasOwn(source, field)) target[field] = clone(source[field])
  else delete target[field]
}

function projectOrder(elements, batch) {
  let ordered = elements
  if (batch.order_after) {
    const present = new Set(elements.map((item) => item.id))
    const known = new Set(batch.order_after.filter((id) => present.has(id)))
    const byId = new Map(elements.map((item) => [item.id, item]))
    const queue = batch.order_after.filter((id) => known.has(id))
    let index = 0
    ordered = elements.map((item) => known.has(item.id) ? byId.get(queue[index++]) : item)
  }
  for (const change of batch.changes.values()) {
    if (change.before || !change.after) continue
    const value = ordered.find((item) => item.id === change.id)
    if (!value) continue
    ordered = ordered.filter((item) => item.id !== change.id)
    const previous = ordered.findIndex((item) => item.id === change.place_after)
    const following = ordered.findIndex((item) => item.id === change.place_before)
    ordered.splice(previous >= 0 ? previous + 1 : following >= 0 ? following : ordered.length, 0, value)
  }
  return ordered
}

function projectBatch(document, batch) {
  if (!batch) return document
  const elements = new Map(document.elements.map((item) => [item.id, item]))
  for (const change of batch.changes.values()) {
    const current = elements.get(change.id)
    if (!change.after) elements.delete(change.id)
    else if (!change.before || !current || change.before.type !== change.after.type) elements.set(change.id, change.after)
    else {
      const next = { ...current }
      const fields = new Set(change.fields)
      if (change.fields.some((key) => geometry.has(key))) for (const key of geometry) fields.add(key)
      if (current.isDeleted && !change.before.isDeleted && !change.after.isDeleted) fields.add('isDeleted')
      for (const key of fields) copyField(next, change.after, key)
      elements.set(change.id, next)
    }
  }
  const ordered = projectOrder([...elements.values()], batch)
  const selection = (batch.selection ?? document.selection).filter((id) => elements.has(id) && !elements.get(id).isDeleted)
  return { ...document, elements: ordered, selection }
}

export class ManualEdits {
  constructor(createId = () => crypto.randomUUID()) {
    this.createId = createId
    this.reset()
  }

  reset() { this.pending = null; this.inflight = null; this.conflicted = false }
  get hasChanges() { return Boolean(this.pending || this.inflight) }

  capture(previous, elements, selection) {
    const old = new Map(previous.elements.map((item) => [item.id, item]))
    const next = new Map(elements.map((item) => [item.id, item]))
    const changed = [...new Set([...old.keys(), ...next.keys()])].filter((id) => !equal(old.get(id), next.get(id)))
    const beforeOrder = previous.elements.map((item) => item.id), afterOrder = elements.map((item) => item.id)
    const orderChanged = !same(beforeOrder, afterOrder)
    if (!changed.length && !orderChanged && same(previous.selection, selection)) return false
    if (!this.pending) this.pending = { mutation_id: this.createId(), documentId: previous.id,
      changes: new Map(), order_before: null, order_after: null, selection: null }
    const batch = this.pending
    if (batch.documentId !== previous.id) throw new Error('Unsaved edits belong to another canvas')
    for (const id of changed) {
      const before = old.get(id) || null, after = next.get(id) || null
      const prior = batch.changes.get(id)
      if (!prior) batch.changes.set(id, { id, before: clone(before), after: clone(after),
        fields: before && after ? fieldsChanged(before, after) : [] })
      else if (!prior.before) {
        if (after) prior.after = clone(after)
        else batch.changes.delete(id)
      } else if (!after || !before || before.type !== after.type) {
        prior.after = clone(after)
        prior.fields = after ? fieldsChanged(prior.before, after) : []
      } else {
        const added = fieldsChanged(before, after)
        const guarded = new Set(prior.fields)
        if (prior.fields.some((key) => geometry.has(key))) for (const key of geometry) guarded.add(key)
        const newGuards = new Set(added)
        if (added.some((key) => geometry.has(key))) for (const key of geometry) newGuards.add(key)
        for (const key of newGuards) if (!guarded.has(key)) {
          copyField(prior.before, before, key)
          if (prior.after) copyField(prior.after, before, key)
        }
        for (const key of added) copyField(prior.after, after, key)
        const actualFields = new Set(fieldsChanged(prior.before, prior.after))
        prior.fields = [...new Set([...prior.fields, ...added])].filter((key) => actualFields.has(key))
        if (equal(prior.before, prior.after)) batch.changes.delete(id)
      }
    }
    const creations = new Set([...batch.changes.values()].filter((item) => !item.before).map((item) => item.id))
    if (orderChanged) {
      const retained = new Set(beforeOrder.filter((id) => next.has(id) && !creations.has(id)))
      const oldKnown = beforeOrder.filter((id) => retained.has(id)), newKnown = afterOrder.filter((id) => retained.has(id))
      if (!same(oldKnown, newKnown)) {
        if (!batch.order_before) batch.order_before = beforeOrder.filter((id) => !creations.has(id))
        batch.order_after = afterOrder.filter((id) => batch.order_before.includes(id))
      } else if (batch.order_before) batch.order_after = afterOrder.filter((id) => batch.order_before.includes(id))
    }
    for (const item of batch.changes.values()) if (!item.before && item.after) {
      const index = afterOrder.indexOf(item.id)
      item.place_after = afterOrder[index - 1] ?? null
      item.place_before = afterOrder[index + 1] ?? null
    }
    batch.selection = [...selection]
    return true
  }

  project(document) { return projectBatch(projectBatch(document, this.inflight), this.pending) }

  start(clientId) {
    if (this.conflicted) throw new Error('请先处理保留的本地修改。')
    if (!this.inflight) { this.inflight = this.pending; this.pending = null }
    if (!this.inflight) return null
    const batch = this.inflight
    return { mutation_id: batch.mutation_id, client_id: clientId, changes: [...batch.changes.values()],
      order_before: batch.order_before, order_after: batch.order_after, selection: batch.selection }
  }

  acknowledge(id) {
    if (this.inflight?.mutation_id !== id) throw new Error('The saved edit does not match this canvas operation')
    this.inflight = null
    this.conflicted = false
  }

  keepLocal(document) {
    const projected = this.project(document)
    this.reset()
    this.capture(document, projected.elements, projected.selection)
    return projected
  }
}
