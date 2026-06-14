<script setup>
import { COMMAND_CAPABILITY_GROUPS } from '../services/capabilityAnnouncement.js'

defineProps({
  show: { type: Boolean, default: false },
})

const emit = defineEmits(['close'])
</script>

<template>
  <div v-if="show" class="notice-backdrop" @click.self="emit('close')">
    <section class="notice" role="dialog" aria-modal="true" aria-labelledby="notice-title">
      <div class="notice-header">
        <div>
          <p class="eyebrow">Voice command guide</p>
          <h2 id="notice-title">支持的语音指令能力</h2>
        </div>
        <button class="icon-btn" aria-label="关闭指令能力公告" @click="emit('close')">&times;</button>
      </div>

      <div class="notice-grid">
        <article v-for="group in COMMAND_CAPABILITY_GROUPS" :key="group.title" class="capability">
          <h3>{{ group.title }}</h3>
          <p>{{ group.summary }}</p>
          <ul>
            <li v-for="example in group.examples" :key="example">{{ example }}</li>
          </ul>
        </article>
      </div>

      <div class="notice-actions">
        <button class="primary-btn" @click="emit('close')">知道了</button>
      </div>
    </section>
  </div>
</template>

<style scoped>
.notice-backdrop {
  position: fixed;
  inset: 0;
  z-index: 28;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 1.25rem;
  background: rgba(2, 6, 23, 0.72);
  backdrop-filter: blur(4px);
}

.notice {
  width: min(860px, calc(100vw - 2.5rem));
  max-height: min(760px, calc(100vh - 2.5rem));
  overflow: auto;
  display: flex;
  flex-direction: column;
  gap: 1rem;
  padding: 1.25rem;
  border: 1px solid rgba(129, 140, 248, 0.28);
  border-radius: 10px;
  background: #172033;
  box-shadow: 0 24px 70px rgba(0, 0, 0, 0.45);
}

.notice-header {
  display: flex;
  justify-content: space-between;
  gap: 1rem;
  align-items: flex-start;
  padding-bottom: 0.75rem;
  border-bottom: 1px solid rgba(148, 163, 184, 0.16);
}

.eyebrow {
  margin-bottom: 0.25rem;
  color: #A5B4FC;
  font-size: 0.72rem;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.notice h2 {
  color: #F8FAFC;
  font-size: 1.25rem;
  letter-spacing: 0;
}

.icon-btn {
  width: 2rem;
  height: 2rem;
  border: 1px solid rgba(148, 163, 184, 0.2);
  border-radius: 6px;
  background: rgba(15, 23, 42, 0.7);
  color: #CBD5E1;
  font-size: 1.25rem;
  cursor: pointer;
}

.notice-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0.75rem;
}

.capability {
  min-height: 150px;
  padding: 0.9rem;
  border: 1px solid rgba(99, 102, 241, 0.16);
  border-radius: 8px;
  background: rgba(15, 23, 42, 0.58);
}

.capability h3 {
  margin-bottom: 0.45rem;
  color: #E0E7FF;
  font-size: 0.98rem;
}

.capability p {
  min-height: 2.6rem;
  color: #CBD5E1;
  font-size: 0.82rem;
  line-height: 1.55;
}

.capability ul {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  margin-top: 0.65rem;
  padding-left: 1rem;
  color: #94A3B8;
  font-size: 0.78rem;
  line-height: 1.45;
}

.notice-actions {
  display: flex;
  justify-content: flex-end;
}

.primary-btn {
  padding: 0.65rem 1rem;
  border: none;
  border-radius: 7px;
  background: #6366F1;
  color: white;
  font-weight: 700;
  cursor: pointer;
}

.icon-btn:hover {
  background: rgba(99, 102, 241, 0.15);
}

.primary-btn:hover {
  background: #4F46E5;
}

@media (max-width: 720px) {
  .notice {
    width: 100%;
    max-height: calc(100vh - 2rem);
    padding: 1rem;
  }

  .notice-grid {
    grid-template-columns: 1fr;
  }

  .capability {
    min-height: auto;
  }

  .capability p {
    min-height: auto;
  }
}
</style>
