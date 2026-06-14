import test from 'node:test'
import assert from 'node:assert/strict'
import { normalizeVoiceTranscript, parseVoiceCommand } from '../src/services/voiceCommand.js'

test('normalizes common speech fillers and punctuation', () => {
  assert.equal(normalizeVoiceTranscript('嗯，麻烦你 画一个红色圆。'), '画一个红色圆')
})

test('recognizes low-latency canvas commands', () => {
  assert.equal(parseVoiceCommand('撤消操作').type, 'undo')
  assert.equal(parseVoiceCommand('再做一次').type, 'redo')
  assert.equal(parseVoiceCommand('清空整个画布').type, 'request_clear')
  assert.equal(parseVoiceCommand('确认清空').type, 'confirm_clear')
  assert.equal(parseVoiceCommand('停止当前绘图').type, 'stop_current')
  assert.equal(parseVoiceCommand('取消正在执行的任务').type, 'stop_current')
  assert.equal(parseVoiceCommand('取消').type, 'cancel')
  assert.equal(parseVoiceCommand('再试一次').type, 'retry')
})

test('keeps drawing instructions for the agent', () => {
  assert.deepEqual(
    parseVoiceCommand('然后，在房子旁边画一棵树'),
    { type: 'draw', text: '在房子旁边画一棵树' },
  )
})

test('does not treat compound drawing requests as destructive local commands', () => {
  assert.equal(parseVoiceCommand('清空画布然后画一个圆').type, 'draw')
})

test('drops common recognition noise without dropping short drawing nouns', () => {
  assert.equal(parseVoiceCommand('嗯，然后').type, 'empty')
  assert.equal(parseVoiceCommand('圆').type, 'draw')
})
