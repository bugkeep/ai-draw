export const DEFAULT_SETTINGS = Object.freeze({
  provider: 'auto',
  apiKey: '',
  model: '',
  speechEnabled: true,
  speechRate: 1.08,
})

const VALID_PROVIDERS = new Set(['auto', 'openai', 'bailian'])

export function normalizeSettings(settings = {}) {
  const speechRate = Number(settings.speechRate)
  return {
    provider: VALID_PROVIDERS.has(settings.provider) ? settings.provider : DEFAULT_SETTINGS.provider,
    apiKey: String(settings.apiKey || '').trim(),
    model: String(settings.model || '').trim(),
    speechEnabled: settings.speechEnabled !== false,
    speechRate: Number.isFinite(speechRate)
      ? Math.min(1.5, Math.max(0.7, speechRate))
      : DEFAULT_SETTINGS.speechRate,
  }
}

export function loadSettings(localStore = localStorage, sessionStore = sessionStorage) {
  let saved = {}
  try {
    saved = JSON.parse(localStore.getItem('ai_draw_settings') || '{}')
  } catch {}
  return normalizeSettings({
    ...saved,
    apiKey: sessionStore.getItem('ai_draw_api_key') || '',
  })
}

export function saveSettings(settings, localStore = localStorage, sessionStore = sessionStorage) {
  const normalized = normalizeSettings(settings)
  const { apiKey, ...persisted } = normalized
  localStore.setItem('ai_draw_settings', JSON.stringify(persisted))
  if (apiKey) sessionStore.setItem('ai_draw_api_key', apiKey)
  else sessionStore.removeItem('ai_draw_api_key')
  return normalized
}

export function buildModelConfig(settings) {
  const normalized = normalizeSettings(settings)
  return {
    ...(normalized.provider !== 'auto' ? { provider: normalized.provider } : {}),
    ...(normalized.apiKey ? { api_key: normalized.apiKey } : {}),
    ...(normalized.model ? { model: normalized.model } : {}),
  }
}
