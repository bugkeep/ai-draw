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

provide('messages', messages)
provide('isProcessing', isProcessing)
provide('status', status)
provide('canvasRef', canvasRef)

function addMessage(text, type = 'user') {
  messages.value.push({ text, type, id: Date.now() })
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
      body: JSON.stringify({ message: text, canvas_state: canvasState }),
    })
    const data = await res.json()

    if (data.code) {
      canvasRef.value?.executeCode(data.code)
    }
    if (data.description) {
      addMessage(data.description, 'assistant')
      speak(data.description)
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

function speak(text) {
  if (!window.speechSynthesis) return
  window.speechSynthesis.cancel()
  const u = new SpeechSynthesisUtterance(text)
  u.lang = 'zh-CN'
  u.rate = 1.0
  window.speechSynthesis.speak(u)
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
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #1a1a2e; color: #eee; height: 100vh; }
.app { display: flex; flex-direction: column; height: 100vh; }
.header { display: flex; justify-content: space-between; align-items: center; padding: 1rem 2rem; background: #16213e; border-bottom: 1px solid #0f3460; }
.header h1 { font-size: 1.5rem; color: #e94560; }
.status { padding: 0.25rem 0.75rem; border-radius: 12px; background: #0f3460; font-size: 0.875rem; }
.main { display: flex; flex: 1; overflow: hidden; }
.canvas-area { flex: 1; display: flex; justify-content: center; align-items: center; background: #16213e; padding: 1rem; }
.sidebar { width: 320px; background: #16213e; border-left: 1px solid #0f3460; display: flex; flex-direction: column; padding: 1rem; gap: 1rem; }
.footer { display: flex; gap: 0.5rem; padding: 0.75rem 2rem; background: #16213e; border-top: 1px solid #0f3460; }
.footer button { padding: 0.5rem 1rem; border: 1px solid #0f3460; border-radius: 4px; background: #1a1a2e; color: #eee; cursor: pointer; }
.footer button:hover { background: #0f3460; }
</style>
