<script setup>
import { ref, onMounted } from 'vue'
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

const CUSTOM_PROPS = ['objectId', 'semanticType', 'name', 'groupId', 'tags']

function saveState() {
  if (!canvas) return
  const json = JSON.stringify(canvas.toJSON(CUSTOM_PROPS))
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
    return true
  }
  return false
}

function redo() {
  if (historyIndex.value < history.value.length - 1) {
    historyIndex.value++
    canvas.loadFromJSON(history.value[historyIndex.value], () => canvas.renderAll())
    return true
  }
  return false
}

function clear() {
  canvas.clear()
  canvas.backgroundColor = '#ffffff'
  canvas.renderAll()
  saveState()
  return true
}

async function executeCode(code) {
  try {
    const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
    const fn = new AsyncFunction('canvas', 'fabric', code)
    await fn(canvas, fabric)
    canvas.requestRenderAll()
    saveState()
    return true
  } catch (e) {
    console.error('Execute code error:', e)
    return false
  }
}

function serializeFill(fill) {
  if (!fill) return null
  if (typeof fill === 'string') return { type: 'solid', color: fill }
  if (fill.type && fill.colorStops) {
    return { type: fill.type, coords: fill.coords, colorStops: fill.colorStops }
  }
  return { type: 'unknown' }
}

function serializeObject(obj) {
  const base = {
    object_id: obj.objectId,
    semantic_type: obj.semanticType,
    name: obj.name,
    group_id: obj.groupId,
    type: obj.type,
    left: Math.round(obj.left || 0),
    top: Math.round(obj.top || 0),
    width: obj.width ? Math.round(obj.width * obj.scaleX) : undefined,
    height: obj.height ? Math.round(obj.height * obj.scaleY) : undefined,
    scale_x: obj.scaleX,
    scale_y: obj.scaleY,
    angle: obj.angle,
    opacity: obj.opacity,
    fill: serializeFill(obj.fill),
    stroke: obj.stroke,
    stroke_width: obj.strokeWidth,
  }
  if (obj.type === 'circle') {
    base.radius = obj.radius ? Math.round(obj.radius * obj.scaleX) : undefined
  }
  if (obj.type === 'ellipse') {
    base.rx = obj.rx ? Math.round(obj.rx * obj.scaleX) : undefined
    base.ry = obj.ry ? Math.round(obj.ry * obj.scaleY) : undefined
  }
  if (obj.type === 'text') {
    base.text = obj.text
    base.font_size = obj.fontSize
  }
  if (obj.type === 'polygon' || obj.type === 'polyline') {
    base.points = obj.points
  }
  if (obj.type === 'path') {
    base.path = obj.path
  }
  if (obj.type === 'image' && obj.getSrc) {
    base.src = obj.getSrc()
  }
  if (obj.type === 'group') {
    base.children = obj.getObjects().map(serializeObject)
  }
  if (obj.clipPath) {
    base.clip_path = serializeObject(obj.clipPath)
  }
  return base
}

function importSVG(url, options = {}) {
  return new Promise((resolve, reject) => {
    if (!canvas) return reject(new Error('Canvas not ready'))
    fabric.loadSVGFromURL(url, (objects, imgOptions) => {
      if (!objects || objects.length === 0) {
        return reject(new Error('SVG returned no objects'))
      }
      const group = fabric.util.groupSVGElements(objects, imgOptions)
      group.set({
        left: options.left ?? 100,
        top: options.top ?? 100,
        objectId: options.objectId || `svg_${Date.now()}`,
        semanticType: options.semanticType || 'svg_asset',
        ...options,
      })
      if (options.width && options.height) {
        group.set({
          scaleX: options.width / (group.width || 1),
          scaleY: options.height / (group.height || 1),
        })
      }
      canvas.add(group)
      canvas.setActiveObject(group)
      canvas.requestRenderAll()
      saveState()
      resolve(group)
    }, reject)
  })
}

function getState() {
  if (!canvas) return { objects: [], canvas_size: { width: 800, height: 600 } }
  return {
    objects: canvas.getObjects().map(serializeObject),
    canvas_size: { width: canvas.getWidth(), height: canvas.getHeight() },
  }
}

defineExpose({ undo, redo, clear, executeCode, importSVG, getState })
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
