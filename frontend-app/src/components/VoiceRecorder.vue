<script setup>
import { ref, inject } from 'vue'

const emit = defineEmits(['transcript'])
const isRecording = ref(false)
const transcript = ref('')
const btnText = ref('Start Recording')
let recognition = null

function initRecognition() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition
  if (!SR) {
    console.warn('Speech Recognition not supported')
    return null
  }
  const r = new SR()
  r.continuous = false
  r.interimResults = true
  r.lang = 'zh-CN'

  r.onresult = (event) => {
    transcript.value = Array.from(event.results)
      .map(result => result[0].transcript)
      .join('')
    if (event.results[0].isFinal && transcript.value.trim()) {
      emit('transcript', transcript.value.trim())
    }
  }

  r.onend = () => {
    isRecording.value = false
    btnText.value = 'Start Recording'
  }

  r.onerror = (event) => {
    console.error('Speech error:', event.error)
    isRecording.value = false
    btnText.value = 'Start Recording'
  }

  return r
}

function toggle() {
  if (isRecording.value) {
    recognition?.stop()
  } else {
    if (!recognition) recognition = initRecognition()
    if (!recognition) return
    isRecording.value = true
    btnText.value = 'Stop Recording'
    recognition.start()
  }
}
</script>

<template>
  <div class="voice-controls">
    <button class="btn-record" :class="{ recording: isRecording }" @click="toggle">
      <span class="mic-icon">🎤</span>
      <span>{{ btnText }}</span>
    </button>
    <div class="transcript">{{ transcript || 'Speak something...' }}</div>
  </div>
</template>

<style scoped>
.voice-controls { display: flex; flex-direction: column; gap: 0.5rem; }
.btn-record {
  width: 100%; padding: 1rem; border: none; border-radius: 8px;
  background: #e94560; color: white; font-size: 1rem; cursor: pointer;
  display: flex; align-items: center; justify-content: center; gap: 0.5rem;
  transition: background 0.2s;
}
.btn-record:hover { background: #c73e54; }
.btn-record.recording { background: #ff6b6b; animation: pulse 1.5s infinite; }
@keyframes pulse { 0%, 100% { transform: scale(1); } 50% { transform: scale(1.02); } }
.mic-icon { font-size: 1.5rem; }
.transcript {
  padding: 0.75rem; background: #0f3460; border-radius: 8px;
  min-height: 3rem; font-style: italic; color: #aaa;
}
</style>
