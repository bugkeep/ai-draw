<script setup>
import AppCanvas from './components/AppCanvas.vue'
import VoiceRecorder from './components/VoiceRecorder.vue'
import ChatLog from './components/ChatLog.vue'
import SettingsPanel from './components/SettingsPanel.vue'
import { ref, provide } from 'vue'

const messages = ref([])
const isProcessing = ref(false)
const status = ref('Ready')
const canvasRef = ref(null)
const provider = ref(localStorage.getItem('provider') || 'openai')
const apiKey = ref(localStorage.getItem('api_key') || '')

provide('messages', messages)
provide('isProcessing', isProcessing)
provide('status', status)
provide('canvasRef', canvasRef)
provide('provider', provider)
provide('apiKey', apiKey)

function addMessage(text, type = 'user', code = '') {
  messages.value.push({ text, type, code, id: Date.now() })
}

async function sendMessage(text) {
  if (isProcessing.value) return
  isProcessing.value = true
  status.value = 'Processing...'
  addMessage(text, 'user')

  try {
    const canvasState = canvasRef.value?.getState() || {}
    const res = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message: text,
        canvas_state: canvasState,
        provider: provider.value,
        api_key: apiKey.value,
      }),
    })
    const data = await res.json()

    if (data.code) {
      canvasRef.value?.executeCode(data.code)
    }
    if (data.description) {
      addMessage(data.description, 'assistant', data.code)
    }
    if (data.error) {
      addMessage(`Error: ${data.error}`, 'system')
    }
  } catch (e) {
    addMessage(`Error: ${e.message}`, 'system')
  } finally {
    isProcessing.value = false
    status.value = 'Ready'
  }
}

provide('sendMessage', sendMessage)
</script>

<template>
  <div class="app">
    <header class="header">
      <h1>AI Voice Draw</h1>
      <span class="status">{{ status }}</span>
    </header>

    <main class="main">
      <div class="canvas-area">
        <AppCanvas ref="canvasRef" />
      </div>
      <aside class="sidebar">
        <VoiceRecorder @transcript="sendMessage" />
        <ChatLog />
        <SettingsPanel />
      </aside>
    </main>

    <footer class="footer">
      <button @click="canvasRef?.undo()">Undo</button>
      <button @click="canvasRef?.redo()">Redo</button>
      <button @click="canvasRef?.clear()">Clear</button>
    </footer>
  </div>
</template>

<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;700&family=Inter:wght@400;500&display=swap');

* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: 'Inter', sans-serif; background: #0F172A; color: #F8FAFC; height: 100vh; }
.app { display: flex; flex-direction: column; height: 100vh; }

.header {
  display: flex; justify-content: space-between; align-items: center;
  padding: 1rem 2rem; background: linear-gradient(135deg, #1E293B 0%, #0F172A 100%);
  border-bottom: 1px solid rgba(99, 102, 241, 0.2);
}
.header h1 {
  font-family: 'Space Grotesk', sans-serif; font-weight: 700;
  font-size: 1.5rem; color: #F8FAFC;
  background: linear-gradient(135deg, #6366F1, #F59E0B);
  -webkit-background-clip: text; -webkit-text-fill-color: transparent;
  background-clip: text;
}
.status {
  padding: 0.375rem 1rem; border-radius: 20px;
  background: rgba(99, 102, 241, 0.15); border: 1px solid rgba(99, 102, 241, 0.3);
  font-size: 0.8rem; font-weight: 500; color: #A5B4FC;
  letter-spacing: 0.02em;
}

.main { display: flex; flex: 1; overflow: hidden; }
.canvas-area {
  flex: 1; display: flex; justify-content: center; align-items: center;
  background: #1E293B; padding: 1.5rem;
  position: relative;
}
.canvas-area::before {
  content: ''; position: absolute; inset: 1rem;
  border: 1px solid rgba(99, 102, 241, 0.1); border-radius: 12px;
  pointer-events: none;
}

.sidebar {
  width: 340px; background: #1E293B;
  border-left: 1px solid rgba(99, 102, 241, 0.15);
  display: flex; flex-direction: column; padding: 1.25rem; gap: 1rem;
  overflow-y: auto;
}

.footer {
  display: flex; gap: 0.75rem; padding: 0.875rem 2rem;
  background: #0F172A; border-top: 1px solid rgba(99, 102, 241, 0.15);
}
.footer button {
  padding: 0.5rem 1.25rem; border: 1px solid rgba(99, 102, 241, 0.3);
  border-radius: 6px; background: rgba(99, 102, 241, 0.1);
  color: #A5B4FC; cursor: pointer; font-weight: 500; font-size: 0.875rem;
  transition: all 0.2s ease;
}
.footer button:hover {
  background: rgba(99, 102, 241, 0.2); border-color: #6366F1;
  transform: translateY(-1px);
}
</style>
