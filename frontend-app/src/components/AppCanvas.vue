<script setup>
import { ref, onMounted, defineExpose } from 'vue'
import { fabric } from 'fabric'

const canvasEl = ref(null)
let canvas = null
const history = ref([])
const historyIndex = ref(-1)

onMounted(() => {
  canvas = new fabric.Canvas(canvasEl.value, {
    backgroundColor: '#ffffff',
    selection: true,
  })
  canvas.on('object:modified', saveState)
  saveState()
})

function saveState() {
  if (!canvas) return
  const json = JSON.stringify(canvas.toJSON())
  if (historyIndex.value < history.value.length - 1) {
    history.value = history.value.slice(0, historyIndex.value + 1)
  }
  history.value.push(json)
  if (history.value.length > 50) {
    history.value.shift()
  } else {
    historyIndex.value++
  }
}

function undo() {
  if (historyIndex.value > 0) {
    historyIndex.value--
    canvas.loadFromJSON(history.value[historyIndex.value], () => canvas.renderAll())
  }
}

function redo() {
  if (historyIndex.value < history.value.length - 1) {
    historyIndex.value++
    canvas.loadFromJSON(history.value[historyIndex.value], () => canvas.renderAll())
  }
}

function clear() {
  canvas.clear()
  canvas.backgroundColor = '#ffffff'
  canvas.renderAll()
  saveState()
}

function executeCode(code) {
  try {
    const wrapped = code
      .split('\n')
      .filter(line => line.trim())
      .map(line => `{ ${line} }`)
      .join('\n')
    const fn = new Function('canvas', 'fabric', wrapped)
    fn(canvas, fabric)
    canvas.renderAll()
    saveState()
    return true
  } catch (e) {
    console.error('Execute code error:', e)
    return false
  }
}

function getState() {
  if (!canvas) return { objects: [], canvas_size: { width: 800, height: 600 } }
  const objects = canvas.getObjects().map(obj => ({
    type: obj.type,
    left: Math.round(obj.left),
    top: Math.round(obj.top),
    width: obj.width ? Math.round(obj.width * obj.scaleX) : undefined,
    height: obj.height ? Math.round(obj.height * obj.scaleY) : undefined,
    radius: obj.radius ? Math.round(obj.radius * obj.scaleX) : undefined,
    fill: obj.fill,
    stroke: obj.stroke,
    text: obj.text,
  }))
  return {
    objects,
    canvas_size: { width: canvas.getWidth(), height: canvas.getHeight() },
  }
}

defineExpose({ undo, redo, clear, executeCode, getState })
</script>

<template>
  <canvas ref="canvasEl" width="800" height="600"></canvas>
</template>

<style scoped>
canvas {
  background: #FAFAF9;
  border-radius: 8px;
  box-shadow:
    0 0 0 1px rgba(99, 102, 241, 0.1),
    0 4px 24px rgba(0, 0, 0, 0.4),
    0 0 60px rgba(99, 102, 241, 0.05);
  transition: box-shadow 0.3s ease;
}
canvas:hover {
  box-shadow:
    0 0 0 1px rgba(99, 102, 241, 0.2),
    0 8px 32px rgba(0, 0, 0, 0.5),
    0 0 80px rgba(99, 102, 241, 0.1);
}
</style>
