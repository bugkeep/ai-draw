<script setup>
import { reactive, watch } from 'vue'
import { normalizeSettings } from '../services/appSettings.js'

const props = defineProps({
  show: { type: Boolean, default: false },
  settings: { type: Object, required: true },
})
const emit = defineEmits(['close', 'save'])
const draft = reactive(normalizeSettings(props.settings))

watch(
  () => [props.show, props.settings],
  () => Object.assign(draft, normalizeSettings(props.settings)),
  { deep: true },
)

function submit() {
  emit('save', normalizeSettings(draft))
}
</script>

<template>
  <div v-if="show" class="settings-backdrop" @click.self="emit('close')">
    <section class="settings" role="dialog" aria-modal="true" aria-labelledby="settings-title">
      <div class="settings-header">
        <div>
          <h2 id="settings-title">Settings</h2>
          <p>Configure model access and voice feedback.</p>
        </div>
        <button class="icon-btn" aria-label="Close settings" @click="emit('close')">&times;</button>
      </div>

      <label>
        Provider
        <select v-model="draft.provider">
          <option value="auto">Auto (server configuration)</option>
          <option value="openai">OpenAI</option>
          <option value="bailian">Alibaba Bailian</option>
        </select>
      </label>

      <label>
        API Key
        <input v-model="draft.apiKey" type="password" autocomplete="off" placeholder="Leave blank to use server key" />
        <span class="field-hint">API Key is kept only for this browser session.</span>
      </label>

      <label>
        Model
        <input
          v-model="draft.model"
          type="text"
          :placeholder="draft.provider === 'bailian' ? 'qwen-plus' : draft.provider === 'openai' ? 'gpt-4o' : 'Use provider default'"
        />
      </label>

      <label class="toggle-row">
        <input v-model="draft.speechEnabled" type="checkbox" />
        <span>Speak operation results</span>
      </label>

      <label>
        Speech rate: {{ Number(draft.speechRate).toFixed(2) }}
        <input v-model.number="draft.speechRate" type="range" min="0.7" max="1.5" step="0.05" />
      </label>

      <div class="settings-actions">
        <button class="secondary-btn" @click="emit('close')">Cancel</button>
        <button class="primary-btn" @click="submit">Save settings</button>
      </div>
    </section>
  </div>
</template>

<style scoped>
.settings-backdrop {
  position: fixed; inset: 0; z-index: 30;
  display: flex; align-items: flex-start; justify-content: flex-end;
  padding: 4.75rem 1.5rem 1.5rem;
  background: rgba(2, 6, 23, 0.72);
  backdrop-filter: blur(4px);
}
.settings {
  width: min(390px, calc(100vw - 3rem));
  display: flex; flex-direction: column; gap: 1rem;
  padding: 1.25rem; background: #172033;
  border-radius: 12px; border: 1px solid rgba(129, 140, 248, 0.3);
  box-shadow: 0 24px 70px rgba(0, 0, 0, 0.45);
}
.settings-header { display: flex; justify-content: space-between; align-items: flex-start; gap: 1rem; }
.settings-header h2 { font-size: 1.1rem; color: #F8FAFC; }
.settings-header p { margin-top: 0.25rem; color: #94A3B8; font-size: 0.78rem; }
.icon-btn {
  width: 2rem; height: 2rem; border: 1px solid rgba(148, 163, 184, 0.2);
  border-radius: 6px; background: rgba(15, 23, 42, 0.7); color: #CBD5E1;
  font-size: 1.25rem; cursor: pointer;
}
.settings label {
  display: flex; flex-direction: column; gap: 0.375rem;
  font-size: 0.8rem; font-weight: 500; color: #94A3B8;
  letter-spacing: 0.02em;
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
.field-hint { font-size: 0.7rem; color: #64748B; font-weight: 400; }
.toggle-row { flex-direction: row !important; align-items: center; }
.toggle-row input { width: auto; accent-color: #6366F1; }
.settings-actions { display: flex; justify-content: flex-end; gap: 0.625rem; margin-top: 0.25rem; }
.settings-actions button {
  padding: 0.625rem 0.9rem; border-radius: 7px; font-weight: 600; cursor: pointer;
}
.secondary-btn { border: 1px solid rgba(148, 163, 184, 0.25); background: transparent; color: #CBD5E1; }
.primary-btn { border: none; background: #6366F1; color: white; }
.icon-btn:hover, .secondary-btn:hover { background: rgba(99, 102, 241, 0.15); }
.primary-btn:hover { background: #4F46E5; }

@media (max-width: 600px) {
  .settings-backdrop { align-items: flex-end; padding: 1rem; }
  .settings { width: 100%; }
}
</style>
