<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import { normalizeVoiceTranscript } from '../services/voiceCommand.js'

const emit = defineEmits(['transcript', 'status', 'error'])
const isRecording = ref(false)
const transcript = ref('')
const btnText = ref('Start listening')
let recognition = null
let shouldListen = true
let suspended = false
let restartTimer = null
let finalEmitTimer = null
let pendingFinalParts = []
let lastFinalText = ''
let lastFinalAt = 0

function emitStatus(value) {
  emit('status', value)
}

function scheduleRestart() {
  if (!shouldListen || suspended || restartTimer) return
  restartTimer = window.setTimeout(() => {
    restartTimer = null
    start()
  }, 350)
}

function flushFinalTranscript() {
  if (finalEmitTimer) window.clearTimeout(finalEmitTimer)
  finalEmitTimer = null
  const finalText = normalizeVoiceTranscript(pendingFinalParts.join(' '))
  pendingFinalParts = []
  const now = Date.now()
  if (finalText && (finalText !== lastFinalText || now - lastFinalAt > 1500)) {
    lastFinalText = finalText
    lastFinalAt = now
    emit('transcript', finalText)
  }
}

function queueFinalTranscript(text) {
  const normalized = normalizeVoiceTranscript(text)
  if (!normalized) return
  if (pendingFinalParts.at(-1) !== normalized) pendingFinalParts.push(normalized)
  if (finalEmitTimer) window.clearTimeout(finalEmitTimer)
  finalEmitTimer = window.setTimeout(flushFinalTranscript, 1200)
}

function initRecognition() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition
  if (!SR) {
    emit('error', '当前浏览器不支持语音识别，请使用最新版 Chrome 或 Edge。')
    return null
  }
  const r = new SR()
  r.continuous = true
  r.interimResults = true
  r.lang = 'zh-CN'

  r.onresult = (event) => {
    const displayParts = []
    const finalParts = []

    for (let index = 0; index < event.results.length; index++) {
      const result = event.results[index]
      const text = result[0]?.transcript || ''
      displayParts.push(text)
      if (index >= event.resultIndex && result.isFinal) finalParts.push(text)
    }

    transcript.value = displayParts.join('')
    queueFinalTranscript(finalParts.join(''))
  }

  r.onstart = () => {
    isRecording.value = true
    btnText.value = 'Stop listening'
    emitStatus('正在持续聆听')
  }

  r.onend = () => {
    isRecording.value = false
    btnText.value = 'Start listening'
    if (shouldListen && !suspended) {
      emitStatus('正在恢复语音识别')
      scheduleRestart()
    }
  }

  r.onerror = (event) => {
    isRecording.value = false
    btnText.value = 'Start listening'
    if (event.error === 'not-allowed' || event.error === 'service-not-allowed') {
      shouldListen = false
      emit('error', '需要授予麦克风权限后才能使用纯语音绘图。')
      return
    }
    if (event.error !== 'no-speech' && event.error !== 'aborted') {
      emit('error', `语音识别暂时不可用：${event.error}`)
    }
  }

  return r
}

function start() {
  shouldListen = true
  suspended = false
  if (!recognition) recognition = initRecognition()
  if (!recognition || isRecording.value) return
  try {
    recognition.start()
  } catch (error) {
    if (error?.name !== 'InvalidStateError') emit('error', `无法启动语音识别：${error.message}`)
  }
}

function stop() {
  shouldListen = false
  suspended = false
  if (restartTimer) window.clearTimeout(restartTimer)
  restartTimer = null
  flushFinalTranscript()
  recognition?.stop()
  emitStatus('语音识别已停止')
}

function suspendForSpeech() {
  suspended = true
  recognition?.stop()
}

function resumeAfterSpeech() {
  suspended = false
  if (shouldListen) scheduleRestart()
}

function toggle() {
  if (shouldListen) stop()
  else start()
}

onMounted(() => window.setTimeout(start, 300))
onUnmounted(() => {
  stop()
  if (finalEmitTimer) window.clearTimeout(finalEmitTimer)
})

defineExpose({ start, stop, suspendForSpeech, resumeAfterSpeech })
</script>

<template>
  <div class="voice-controls">
    <button class="btn-record" :class="{ recording: isRecording }" @click="toggle">
      <span class="mic-icon">MIC</span>
      <span>{{ btnText }}</span>
    </button>
    <div class="transcript">{{ transcript || '请直接说出绘图指令…' }}</div>
  </div>
</template>

<style scoped>
.voice-controls { display: flex; flex-direction: column; gap: 0.75rem; }

.btn-record {
  width: 100%; padding: 1rem; border: none; border-radius: 8px;
  background: linear-gradient(135deg, #6366F1 0%, #818CF8 100%);
  color: white; font-size: 0.95rem; font-weight: 500; cursor: pointer;
  display: flex; align-items: center; justify-content: center; gap: 0.625rem;
  transition: all 0.25s ease; position: relative; overflow: hidden;
}
.btn-record::before {
  content: ''; position: absolute; inset: 0;
  background: linear-gradient(135deg, transparent 0%, rgba(255,255,255,0.1) 100%);
  opacity: 0; transition: opacity 0.25s;
}
.btn-record:hover::before { opacity: 1; }
.btn-record:hover { transform: translateY(-2px); box-shadow: 0 4px 20px rgba(99, 102, 241, 0.4); }

.btn-record.recording {
  background: linear-gradient(135deg, #EF4444 0%, #F87171 100%);
  animation: pulse 2s ease-in-out infinite;
}
@keyframes pulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(239, 68, 68, 0.4); }
  50% { box-shadow: 0 0 0 12px rgba(239, 68, 68, 0); }
}

.mic-icon { font-size: 0.75rem; font-weight: 700; letter-spacing: 0.08em; }

.transcript {
  padding: 0.875rem; background: rgba(30, 41, 59, 0.8);
  border: 1px solid rgba(99, 102, 241, 0.15); border-radius: 8px;
  min-height: 3rem; font-size: 0.875rem; color: #94A3B8;
  line-height: 1.5;
}
</style>
