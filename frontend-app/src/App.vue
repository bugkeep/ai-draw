<script setup>
import AppCanvas from './components/AppCanvas.vue'
import VoiceRecorder from './components/VoiceRecorder.vue'
import ChatLog from './components/ChatLog.vue'
import AssetCandidatePanel from './components/AssetCandidatePanel.vue'
import SettingsPanel from './components/SettingsPanel.vue'
import { ref, provide, onMounted, onUnmounted } from 'vue'
import { parseAssetCandidatesFromDescription } from './services/assetApi.js'
import { buildModelConfig, loadSettings, saveSettings } from './services/appSettings.js'
import { parseVoiceCommand } from './services/voiceCommand.js'

const messages = ref([])
const isProcessing = ref(false)
const status = ref('Ready')
const canvasRef = ref(null)
const voiceRef = ref(null)
const appSettings = ref(loadSettings())
const showSettings = ref(false)
const events = ref([])
const wsConnected = ref(false)
const assetCandidates = ref([])
const pendingCommands = ref([])
let ws = null
let queueRunning = false
let pendingClearConfirmation = false
let lastRemoteCommand = ''
let activeRequestController = null

provide('messages', messages)
provide('isProcessing', isProcessing)
provide('status', status)
provide('canvasRef', canvasRef)
provide('events', events)
provide('wsConnected', wsConnected)

function addMessage(text, type = 'user', code = '', includeInHistory = true) {
  messages.value.push({ text, type, code, includeInHistory, id: Date.now() })
}

function addEvent(eventType, data) {
  events.value.push({ type: eventType, data, id: Date.now(), ts: new Date().toLocaleTimeString() })
  if (events.value.length > 100) events.value.shift()
}

function speak(text) {
  const message = String(text || '').trim().slice(0, 220)
  if (!appSettings.value.speechEnabled || !message || !window.speechSynthesis) return Promise.resolve()

  voiceRef.value?.suspendForSpeech()
  window.speechSynthesis.cancel()

  return new Promise((resolve) => {
    const utterance = new SpeechSynthesisUtterance(message)
    utterance.lang = 'zh-CN'
    utterance.rate = appSettings.value.speechRate
    const finish = () => {
      voiceRef.value?.resumeAfterSpeech()
      resolve()
    }
    utterance.onend = finish
    utterance.onerror = finish
    window.speechSynthesis.speak(utterance)
  })
}

function connectWs() {
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
  ws = new WebSocket(`${protocol}//${location.host}/ws`)
  ws.onopen = () => { wsConnected.value = true }
  ws.onclose = () => {
    wsConnected.value = false
    setTimeout(connectWs, 3000)
  }
  ws.onmessage = (e) => {
    try {
      const msg = JSON.parse(e.data)
      if (msg.type === 'event') {
        addEvent(msg.event_type, msg.data)
        handleEvent(msg.event_type, msg.data)
      }
    } catch {}
  }
  ws.onerror = () => {}
}

function handleEvent(eventType, data) {
  switch (eventType) {
    case 'agent_start':
      status.value = 'Agent processing...'
      break
    case 'llm_request':
      status.value = `LLM calling ${data.model || ''}...`
      break
    case 'tool_call': {
      const toolName = data.tool_name || data.name || ''
      status.value = `Executing ${toolName}...`
      if (toolName === 'search_vector_asset') {
        assetCandidates.value = []
      }
      break
    }
    case 'tool_result':
    case 'tool_error':
      status.value = 'Processing...'
      break
    case 'llm_response':
      status.value = 'Processing...'
      break
    case 'agent_stop':
      status.value = 'Ready'
      break
    case 'agent_error':
      status.value = 'Error'
      break
  }
}

function buildConversationHistory() {
  return messages.value
    .filter(message => message.type === 'user' || message.type === 'assistant')
    .filter(message => message.includeInHistory !== false)
    .slice(-8)
    .map(message => ({
      role: message.type,
      content: message.text,
    }))
}

async function executeRemoteCommand(text) {
  const history = buildConversationHistory()
  addMessage(text, 'user')
  status.value = pendingCommands.value.length
    ? `Processing... ${pendingCommands.value.length} queued`
    : 'Processing...'
  const requestController = new AbortController()
  activeRequestController = requestController

  try {
    const canvasState = canvasRef.value?.getState() || {}
    const res = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      signal: requestController.signal,
      body: JSON.stringify({
        message: text,
        canvas_state: canvasState,
        history,
        ...buildModelConfig(appSettings.value),
      }),
    })
    const data = await res.json()
    if (requestController.signal.aborted) return

    const assistantText = data.description || data.content
    if (assistantText) {
      const parsed = parseAssetCandidatesFromDescription(data.description)
      if (parsed.length > 0) {
        assetCandidates.value = parsed
      }
      addMessage(assistantText, 'assistant', data.code)
    }
    if (data.code) {
      const executed = await canvasRef.value?.executeCode(data.code)
      if (!executed) throw new Error('画布执行失败')
    }
    if (data.error) {
      addMessage(`Error: ${data.error}`, 'system')
      await speak(`指令执行失败，${data.error}`)
    } else {
      const elapsed = data.latency_ms ? `，耗时${Math.round(data.latency_ms / 100) / 10}秒` : ''
      void speak(`${data.content || '绘图操作已完成'}${elapsed}`)
    }
  } catch (e) {
    if (e.name === 'AbortError') return
    addMessage(`Error: ${e.message}`, 'system')
    await speak(`指令执行失败，${e.message}`)
  } finally {
    if (activeRequestController === requestController) activeRequestController = null
  }
}

async function drainCommandQueue() {
  if (queueRunning) return
  queueRunning = true
  isProcessing.value = true

  try {
    while (pendingCommands.value.length > 0) {
      const command = pendingCommands.value.shift()
      lastRemoteCommand = command
      await executeRemoteCommand(command)
    }
  } finally {
    queueRunning = false
    isProcessing.value = false
    status.value = 'Ready · listening'
  }
}

function enqueueRemoteCommand(text) {
  pendingCommands.value.push(text)
  if (queueRunning) status.value = `Processing... ${pendingCommands.value.length} queued`
  drainCommandQueue()
}

async function handleTranscript(rawText) {
  const command = parseVoiceCommand(rawText)
  if (command.type === 'empty') return

  if (command.type === 'undo') {
    pendingClearConfirmation = false
    const changed = canvasRef.value?.undo()
    addMessage(command.text, 'user', '', false)
    await speak(changed ? '已撤销上一步' : '没有可以撤销的操作')
    return
  }
  if (command.type === 'redo') {
    pendingClearConfirmation = false
    const changed = canvasRef.value?.redo()
    addMessage(command.text, 'user', '', false)
    await speak(changed ? '已恢复上一步' : '没有可以恢复的操作')
    return
  }
  if (command.type === 'request_clear') {
    pendingClearConfirmation = true
    addMessage(command.text, 'user', '', false)
    await speak('清空画布会删除全部内容，请说确认清空或取消')
    return
  }
  if (command.type === 'confirm_clear') {
    addMessage(command.text, 'user', '', false)
    if (pendingClearConfirmation) {
      pendingClearConfirmation = false
      canvasRef.value?.clear()
      await speak('画布已清空')
    } else {
      await speak('当前没有等待确认的清空操作')
    }
    return
  }
  if (command.type === 'cancel') {
    pendingClearConfirmation = false
    addMessage(command.text, 'user', '', false)
    await speak('已取消')
    return
  }
  if (command.type === 'stop_current') {
    pendingClearConfirmation = false
    const queuedCount = pendingCommands.value.length
    pendingCommands.value.splice(0)
    const hadActiveRequest = Boolean(activeRequestController)
    activeRequestController?.abort()
    addMessage(command.text, 'user', '', false)
    await speak(
      hadActiveRequest || queuedCount
        ? '已停止当前绘图并清除等待中的指令'
        : '当前没有正在执行的绘图指令',
    )
    return
  }
  if (command.type === 'retry') {
    pendingClearConfirmation = false
    addMessage(command.text, 'user', '', false)
    if (lastRemoteCommand) enqueueRemoteCommand(lastRemoteCommand)
    else await speak('还没有可以重试的绘图指令')
    return
  }

  pendingClearConfirmation = false
  enqueueRemoteCommand(command.text)
}

function sendMessage(text) {
  enqueueRemoteCommand(text)
}

function handleVoiceStatus(text) {
  if (!isProcessing.value) status.value = text
}

function handleVoiceError(text) {
  addMessage(text, 'system')
  status.value = 'Voice unavailable'
}

function handleSaveSettings(settings) {
  appSettings.value = saveSettings(settings)
  showSettings.value = false
  status.value = 'Settings saved'
}

onMounted(connectWs)
onUnmounted(() => { if (ws) ws.close() })

provide('sendMessage', sendMessage)
provide('assetCandidates', assetCandidates)
</script>

<template>
  <div class="app">
    <header class="header">
      <h1>AI Voice Draw</h1>
      <div class="header-right">
        <span class="ws-status" :class="{ connected: wsConnected }">{{ wsConnected ? 'WS' : '...' }}</span>
        <span class="status">{{ status }}</span>
        <button class="settings-button" aria-label="Open settings" title="Settings" @click="showSettings = true">
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M12 8.25A3.75 3.75 0 1 0 12 15.75 3.75 3.75 0 0 0 12 8.25Z" />
            <path d="M19.1 13.5a7.7 7.7 0 0 0 .05-3l2-1.55-2-3.45-2.35.95a8.2 8.2 0 0 0-2.6-1.5L13.85 2h-4l-.35 2.95a8.2 8.2 0 0 0-2.6 1.5L4.55 5.5l-2 3.45 2 1.55a7.7 7.7 0 0 0 .05 3l-2.05 1.55 2 3.45 2.4-.95a8.2 8.2 0 0 0 2.55 1.5l.35 2.95h4l.35-2.95a8.2 8.2 0 0 0 2.55-1.5l2.4.95 2-3.45-2.05-1.55Z" />
          </svg>
        </button>
      </div>
    </header>

    <main class="main">
      <div class="canvas-area">
        <AppCanvas ref="canvasRef" />
      </div>
      <aside class="sidebar">
        <VoiceRecorder
          ref="voiceRef"
          @transcript="handleTranscript"
          @status="handleVoiceStatus"
          @error="handleVoiceError"
        />
        <ChatLog />
        <AssetCandidatePanel :candidates="assetCandidates" />
        <div class="events-panel" v-if="events.length">
          <div class="events-title">Event Stream</div>
          <div class="events-list">
            <div v-for="ev in events.slice(-10).reverse()" :key="ev.id" class="event-item">
              <span class="event-ts">{{ ev.ts }}</span>
              <span class="event-type">{{ ev.type }}</span>
            </div>
          </div>
        </div>
      </aside>
    </main>

    <footer class="footer">
      <span>Voice shortcuts: “撤销” · “重做” · “清空画布” · “确认清空” · “停止当前绘图” · “重试”</span>
      <span v-if="pendingCommands.length">Queued: {{ pendingCommands.length }}</span>
    </footer>
    <SettingsPanel
      :show="showSettings"
      :settings="appSettings"
      @close="showSettings = false"
      @save="handleSaveSettings"
    />
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
.header-right { display: flex; align-items: center; gap: 0.75rem; }
.settings-button {
  width: 2.25rem; height: 2.25rem; display: grid; place-items: center;
  border: 1px solid rgba(99, 102, 241, 0.3); border-radius: 50%;
  background: rgba(99, 102, 241, 0.12); color: #A5B4FC; cursor: pointer;
  transition: all 0.2s ease;
}
.settings-button svg { width: 1.15rem; height: 1.15rem; fill: none; stroke: currentColor; stroke-width: 1.7; }
.settings-button:hover { color: #F8FAFC; background: rgba(99, 102, 241, 0.3); transform: rotate(25deg); }
.ws-status {
  padding: 0.25rem 0.5rem; border-radius: 4px; font-size: 0.7rem; font-weight: 600;
  background: rgba(239, 68, 68, 0.2); color: #F87171; letter-spacing: 0.05em;
}
.ws-status.connected {
  background: rgba(34, 197, 94, 0.2); color: #4ADE80;
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
.events-panel {
  background: rgba(15, 23, 42, 0.6); border-radius: 8px; padding: 0.75rem;
  border: 1px solid rgba(99, 102, 241, 0.1);
}
.events-title {
  font-size: 0.75rem; font-weight: 600; color: #64748B;
  text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 0.5rem;
}
.events-list { display: flex; flex-direction: column; gap: 0.25rem; }
.event-item {
  display: flex; gap: 0.5rem; font-size: 0.7rem;
  padding: 0.25rem 0; border-bottom: 1px solid rgba(99, 102, 241, 0.05);
}
.event-ts { color: #475569; min-width: 65px; }
.event-type { color: #A5B4FC; }

.footer {
  display: flex; justify-content: space-between; gap: 0.75rem; padding: 0.875rem 2rem;
  background: #0F172A; border-top: 1px solid rgba(99, 102, 241, 0.15);
  color: #94A3B8; font-size: 0.8rem;
}
</style>
