import test from 'node:test'
import assert from 'node:assert/strict'
import { buildSuccessSpeech } from '../src/services/responseFeedback.js'

test('does not speak successful responses that contain executable code', () => {
  assert.equal(buildSuccessSpeech({
    content: 'const circle = new fabric.Circle()',
    code: 'canvas.add(new fabric.Circle())',
    latency_ms: 1250,
  }), '')
})

test('keeps spoken feedback for successful responses without code', () => {
  assert.equal(buildSuccessSpeech({
    content: '已找到三个图标，请说第几个',
    latency_ms: 1250,
  }), '已找到三个图标，请说第几个，耗时1.3秒')
})
