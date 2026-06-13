<script setup>
import { inject } from 'vue'

const provider = inject('provider')
const apiKey = inject('apiKey')

function save() {
  localStorage.setItem('api_key', apiKey.value)
  localStorage.setItem('provider', provider.value)
}
</script>

<template>
  <div class="settings">
    <label>
      Provider
      <select v-model="provider" @change="save">
        <option value="openai">OpenAI</option>
        <option value="bailian">阿里百炼</option>
      </select>
    </label>
    <label>
      API Key
      <input
        type="password"
        v-model="apiKey"
        :placeholder="provider === 'bailian' ? 'sk-...' : 'sk-...'"
        @change="save"
      />
    </label>
    <div class="hint" v-if="provider === 'bailian'">
      模型: qwen-plus
    </div>
  </div>
</template>

<style scoped>
.settings {
  display: flex; flex-direction: column; gap: 0.875rem;
  padding: 1rem; background: rgba(30, 41, 59, 0.5);
  border-radius: 8px; border: 1px solid rgba(99, 102, 241, 0.1);
}
.settings label {
  display: flex; flex-direction: column; gap: 0.375rem;
  font-size: 0.8rem; font-weight: 500; color: #94A3B8;
  text-transform: uppercase; letter-spacing: 0.05em;
}
.settings input, .settings select {
  width: 100%; padding: 0.625rem 0.75rem;
  border: 1px solid rgba(99, 102, 241, 0.2); border-radius: 6px;
  background: rgba(15, 23, 42, 0.8); color: #F8FAFC;
  font-size: 0.875rem; transition: all 0.2s ease;
}
.settings input:focus, .settings select:focus {
  outline: none; border-color: #6366F1;
  box-shadow: 0 0 0 3px rgba(99, 102, 241, 0.15);
}
.settings input::placeholder { color: #475569; }
.hint {
  font-size: 0.75rem; color: #64748B;
  padding: 0.5rem; background: rgba(99, 102, 241, 0.05);
  border-radius: 4px; text-align: center;
}
</style>
