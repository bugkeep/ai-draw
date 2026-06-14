export function getAssetContentUrl(cacheKey) {
  return `/assets/content/${cacheKey}.svg`
}

export function getAssetPreviewUrl(cacheKey) {
  return `/assets/preview/${cacheKey}`
}

export async function fetchAssetMetadata(cacheKey) {
  const res = await fetch(`/assets/metadata/${cacheKey}`)
  if (!res.ok) {
    if (res.status === 404) return null
    throw new Error(`Failed to fetch metadata: ${res.status}`)
  }
  return res.json()
}

export function buildIconifyPreviewUrl(assetId) {
  if (!assetId || !assetId.startsWith('iconify:')) return null
  const path = assetId.slice('iconify:'.length)
  const [prefix, ...nameParts] = path.split(':')
  const name = nameParts.join(':')
  if (!prefix || !name) return null
  return `https://api.iconify.design/${prefix}/${name}.svg?height=64`
}

export function parseAssetCandidatesFromDescription(text) {
  if (!text) return []
  const candidates = []
  const re = /\[\s*([^\]]+?)\s*\]\s+([^(]+?)\s*\(score=(\d+)%/g
  let match
  while ((match = re.exec(text)) !== null) {
    const assetId = match[1].trim()
    const title = match[2].trim()
    const score = parseInt(match[3], 10)
    const previewUrl = buildIconifyPreviewUrl(assetId)
    candidates.push({ assetId, title, score, previewUrl })
  }
  return candidates
}
