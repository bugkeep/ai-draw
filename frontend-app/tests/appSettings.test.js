import test from 'node:test'
import assert from 'node:assert/strict'
import {
  buildModelConfig,
  loadSettings,
  normalizeSettings,
  saveSettings,
} from '../src/services/appSettings.js'

function memoryStorage() {
  const values = new Map()
  return {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, String(value)),
    removeItem: (key) => values.delete(key),
  }
}

test('normalizes invalid values and bounds speech rate', () => {
  assert.deepEqual(normalizeSettings({
    provider: 'unknown',
    apiKey: '  secret  ',
    model: '  qwen-plus  ',
    speechEnabled: false,
    speechRate: 9,
  }), {
    provider: 'auto',
    apiKey: 'secret',
    model: 'qwen-plus',
    speechEnabled: false,
    speechRate: 1.5,
  })
})

test('only sends explicit model configuration overrides', () => {
  assert.deepEqual(buildModelConfig({}), {})
  assert.deepEqual(buildModelConfig({
    provider: 'bailian',
    apiKey: 'secret',
    model: 'qwen-plus',
  }), {
    provider: 'bailian',
    api_key: 'secret',
    model: 'qwen-plus',
  })
})

test('keeps API key in session storage and other settings locally', () => {
  const localStore = memoryStorage()
  const sessionStore = memoryStorage()

  saveSettings({
    provider: 'openai',
    apiKey: 'session-secret',
    model: 'gpt-4o',
    speechEnabled: false,
    speechRate: 0.8,
  }, localStore, sessionStore)

  assert.equal(JSON.parse(localStore.getItem('ai_draw_settings')).apiKey, undefined)
  assert.equal(sessionStore.getItem('ai_draw_api_key'), 'session-secret')
  assert.deepEqual(loadSettings(localStore, sessionStore), {
    provider: 'openai',
    apiKey: 'session-secret',
    model: 'gpt-4o',
    speechEnabled: false,
    speechRate: 0.8,
  })
})
