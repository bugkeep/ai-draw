<script setup>
import { ref } from 'vue'

const apiKey = ref(localStorage.getItem('api_key') || '')
const provider = ref(localStorage.getItem('provider') || 'openai')

function save() {
  localStorage.setItem('api_key', apiKey.value)
  localStorage.setItem('provider', provider.value)
}
</script>

<template>
  <div class="settings">
    <label>
      API Key:
      <input type="password" v-model="apiKey" placeholder="sk-..." @change="save" />
    </label>
    <label>
      Provider:
      <select v-model="provider" @change="save">
        <option value="openai">OpenAI</option>
        <option value="claude">Claude</option>
      </select>
    </label>
  </div>
</template>

<style scoped>
.settings { display: flex; flex-direction: column; gap: 0.5rem; }
.settings label { font-size: 0.875rem; color: #aaa; }
.settings input, .settings select {
  width: 100%; padding: 0.5rem; margin-top: 0.25rem;
  border: 1px solid #0f3460; border-radius: 4px;
  background: #1a1a2e; color: #eee;
}
</style>
