const editorMetadata = new Set(['version', 'versionNonce', 'updated', 'index'])

export function sceneSnapshotSignature(elements, selection) {
  const content = elements.map((element) => ({
    ...Object.fromEntries(Object.entries(element).filter(([key]) => !editorMetadata.has(key))),
    boundElements: element.boundElements || [],
  }))
  return JSON.stringify({ elements: content, selection }, (_key, value) =>
    value && typeof value === 'object' && !Array.isArray(value) ?
      Object.fromEntries(Object.keys(value).sort().map((key) => [key, value[key]])) : value)
}

// Return null when a snapshot is required; stale patches leave the document untouched.
export function applyScenePatch(document, patch) {
  if (!document || !patch || patch.id !== document.id || !Number.isInteger(patch.version)) return null
  if (patch.version <= document.version) return document
  if (patch.base_version !== document.version || patch.version !== patch.base_version + 1 ||
      !Array.isArray(patch.upserts) || !Array.isArray(patch.removed_ids) || !Array.isArray(patch.selection)) return null
  const removed = new Set(patch.removed_ids)
  const updates = new Map(patch.upserts.map((element) => [element?.id, element]))
  if (removed.size !== patch.removed_ids.length || updates.size !== patch.upserts.length ||
      [...removed].some((id) => typeof id !== 'string' || updates.has(id)) ||
      [...updates.keys()].some((id) => typeof id !== 'string')) return null
  const elements = new Map(document.elements.filter((element) => !removed.has(element.id))
    .map((element) => [element.id, element]))
  for (const [id, element] of updates) elements.set(id, element)
  let ordered = [...elements.values()]
  if (patch.order !== undefined) {
    if (!Array.isArray(patch.order) || patch.order.length !== elements.size ||
        new Set(patch.order).size !== elements.size || patch.order.some((id) => !elements.has(id))) return null
    ordered = patch.order.map((id) => elements.get(id))
  }
  if (patch.selection.some((id) => !elements.has(id))) return null
  return { ...document, title: patch.title, version: patch.version, updated_at: patch.updated_at,
    elements: ordered, selection: patch.selection }
}

export function replayScenePatches(document, events) {
  let current = document
  const pending = []
  for (const event of [...events].sort((a, b) => a.payload.patch.version - b.payload.patch.version)) {
    if (event.payload.patch.id !== current.id || !Number.isInteger(event.payload.patch.version)) continue
    const next = applyScenePatch(current, event.payload.patch)
    if (next === null) pending.push(event)
    else current = next
  }
  return { document: current, pending }
}
