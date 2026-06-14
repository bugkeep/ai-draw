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

.mic-icon { font-size: 1.25rem; }

.transcript {
  padding: 0.875rem; background: rgba(30, 41, 59, 0.8);
  border: 1px solid rgba(99, 102, 241, 0.15); border-radius: 8px;
  min-height: 3rem; font-size: 0.875rem; color: #94A3B8;
  line-height: 1.5;
}
</style>
