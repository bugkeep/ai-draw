<script setup>
defineProps({
  candidates: { type: Array, default: () => [] },
})

function scoreColor(score) {
  if (score >= 90) return '#4ADE80'
  if (score >= 70) return '#FBBF24'
  return '#F87171'
}

function previewFailed(ev) {
  ev.target.style.display = 'none'
}
</script>

<template>
  <div class="asset-panel">
    <div class="asset-header">
      <span class="asset-title">Voice-selected assets</span>
    </div>

    <div v-if="candidates.length > 0" class="candidates-list">
      <div v-for="c in candidates" :key="c.assetId" class="candidate-card">
        <div class="preview-wrap">
          <img
            v-if="c.previewUrl"
            :src="c.previewUrl"
            :alt="c.title"
            class="preview-img"
            @error="previewFailed"
          />
          <div v-else class="no-preview">?</div>
        </div>
        <div class="candidate-info">
          <div class="candidate-name" :title="c.assetId">{{ c.title }}</div>
          <div class="score-bar-wrap">
            <div
              class="score-bar"
              :style="{ width: c.score + '%', backgroundColor: scoreColor(c.score) }"
            ></div>
          </div>
          <div class="score-label">{{ c.score }}%</div>
        </div>
      </div>
    </div>

    <div v-else class="empty-hint">
      Ask by voice to draw an icon or replace an object.
    </div>
  </div>
</template>

<style scoped>
.asset-panel {
  background: rgba(30, 41, 59, 0.5);
  border: 1px solid rgba(99, 102, 241, 0.15);
  border-radius: 8px;
  padding: 0.75rem;
}

.asset-header {
  display: flex; justify-content: space-between; align-items: center;
  margin-bottom: 0.625rem;
}

.asset-title {
  font-size: 0.75rem; font-weight: 600; color: #64748B;
  text-transform: uppercase; letter-spacing: 0.05em;
}

.candidates-list {
  display: flex; flex-direction: column; gap: 0.5rem;
  max-height: 280px; overflow-y: auto;
}
.candidates-list::-webkit-scrollbar { width: 4px; }
.candidates-list::-webkit-scrollbar-track { background: transparent; }
.candidates-list::-webkit-scrollbar-thumb { background: rgba(99, 102, 241, 0.3); border-radius: 2px; }

.candidate-card {
  display: flex; align-items: center; gap: 0.5rem;
  padding: 0.5rem; border-radius: 6px;
  background: rgba(15, 23, 42, 0.6);
  border: 1px solid rgba(99, 102, 241, 0.08);
  transition: border-color 0.2s;
}
.candidate-card:hover {
  border-color: rgba(99, 102, 241, 0.25);
}

.preview-wrap {
  width: 40px; height: 40px; flex-shrink: 0;
  display: flex; align-items: center; justify-content: center;
  background: rgba(99, 102, 241, 0.05);
  border-radius: 4px; overflow: hidden;
}

.preview-img {
  max-width: 36px; max-height: 36px; object-fit: contain;
}

.no-preview {
  font-size: 1rem; color: #475569; font-weight: 700;
}

.candidate-info {
  flex: 1; min-width: 0;
}

.candidate-name {
  font-size: 0.8rem; font-weight: 500; color: #E2E8F0;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  margin-bottom: 0.25rem;
}

.score-bar-wrap {
  height: 4px; background: rgba(99, 102, 241, 0.1); border-radius: 2px;
  overflow: hidden; margin-bottom: 0.125rem;
}

.score-bar {
  height: 100%; border-radius: 2px;
  transition: width 0.3s ease;
}

.score-label {
  font-size: 0.65rem; color: #64748B;
}

.empty-hint {
  font-size: 0.8rem; color: #475569; text-align: center;
  padding: 1rem 0; line-height: 1.5;
}
</style>
