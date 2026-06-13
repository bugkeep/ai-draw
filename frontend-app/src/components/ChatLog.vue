<script setup>
import { inject, nextTick, ref, watch } from 'vue'

const messages = inject('messages')
const chatLog = ref(null)

watch(messages, async () => {
  await nextTick()
  if (chatLog.value) {
    chatLog.value.scrollTop = chatLog.value.scrollHeight
  }
}, { deep: true })

function escapeHtml(text) {
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}
</script>

<template>
  <div class="chat-log" ref="chatLog">
    <div v-if="messages.length === 0" class="chat-message system">
      <p>Say "draw a circle" or "画一个笑脸" to start!</p>
    </div>
    <div v-for="msg in messages" :key="msg.id" class="chat-message" :class="msg.type">
      <p v-html="escapeHtml(msg.text)"></p>
    </div>
  </div>
</template>

<style scoped>
.chat-log { flex: 1; overflow-y: auto; }
.chat-message { padding: 0.75rem; margin-bottom: 0.5rem; border-radius: 8px; background: #0f3460; }
.chat-message.user { background: #1a3a5c; }
.chat-message.assistant { background: #0f3460; border-left: 3px solid #4ecdc4; }
.chat-message.system { background: #1a1a40; text-align: center; font-size: 0.875rem; color: #888; }
</style>
