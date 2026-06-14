import test from 'node:test'
import assert from 'node:assert/strict'
import {
  ANNOUNCEMENT_STORAGE_KEY,
  COMMAND_CAPABILITY_GROUPS,
  rememberCapabilityNoticeDismissed,
  shouldShowCapabilityNotice,
} from '../src/services/capabilityAnnouncement.js'

function createMemoryStorage() {
  const values = new Map()
  return {
    getItem(key) {
      return values.has(key) ? values.get(key) : null
    },
    setItem(key, value) {
      values.set(key, String(value))
    },
  }
}

test('describes expected voice command capability groups', () => {
  const titles = COMMAND_CAPABILITY_GROUPS.map(group => group.title)
  assert.deepEqual(titles, [
    '绘制与生成',
    '选择与编辑',
    '变换与排版',
    '样式与滤镜',
    '对象组织',
    '低延迟控制',
  ])
  assert.ok(COMMAND_CAPABILITY_GROUPS.every(group => group.examples.length >= 3))
})

test('shows capability notice until dismissed', () => {
  const storage = createMemoryStorage()
  assert.equal(shouldShowCapabilityNotice(storage), true)

  rememberCapabilityNoticeDismissed(storage)

  assert.equal(storage.getItem(ANNOUNCEMENT_STORAGE_KEY), '1')
  assert.equal(shouldShowCapabilityNotice(storage), false)
})
