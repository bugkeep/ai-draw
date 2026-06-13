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
      <pre v-if="msg.code" class="code-block"><code>{{ msg.code }}</code></pre>
    </div>
  </div>
</template>

<style scoped>
.chat-log {
  flex: 1; overflow-y: auto; display: flex; flex-direction: column; gap: 0.5rem;
  padding-right: 0.25rem;
}
.chat-log::-webkit-scrollbar { width: 4px; }
.chat-log::-webkit-scrollbar-track { background: transparent; }
.chat-log::-webkit-scrollbar-thumb { background: rgba(99, 102, 241, 0.3); border-radius: 2px; }

.chat-message {
  padding: 0.75rem 1rem; border-radius: 8px;
  background: rgba(30, 41, 59, 0.6);
  font-size: 0.875rem; line-height: 1.6;
  border: 1px solid rgba(99, 102, 241, 0.08);
}
.chat-message.user {
  background: rgba(99, 102, 241, 0.12);
  border-color: rgba(99, 102, 241, 0.2);
  margin-left: 1rem;
}
.chat-message.assistant {
  background: rgba(30, 41, 59, 0.8);
  border-left: 3px solid #F59E0B;
}
.chat-message.system {
  background: rgba(99, 102, 241, 0.08);
  text-align: center; font-size: 0.8rem; color: #64748B;
  border: 1px dashed rgba(99, 102, 241, 0.2);
}
.code-block {
  margin-top: 0.5rem; padding: 0.5rem 0.75rem;
  background: rgba(15, 23, 42, 0.8); border-radius: 6px;
  font-size: 0.75rem; line-height: 1.5; overflow: auto;
  border: 1px solid rgba(99, 102, 241, 0.15);
  color: #A5B4FC; white-space: pre; max-height: 200px;
}
.code-block code {
  font-family: 'Fira Code', monospace;
}
</style>
