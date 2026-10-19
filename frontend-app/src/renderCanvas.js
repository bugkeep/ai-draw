import { exportToCanvas, getCommonBounds, restoreElements } from '@excalidraw/excalidraw'
import '@excalidraw/excalidraw/index.css'

window.renderDrawing = async ({ elements, region, max_size = 1280 }) => {
  const restored = restoreElements(elements.filter((item) => !item.isDeleted), null)
  const bounds = restored.length ? getCommonBounds(restored) : [0, 0, 960, 720]
  const view = region || { x: bounds[0] - 16, y: bounds[1] - 16,
    width: Math.max(32, bounds[2] - bounds[0] + 32), height: Math.max(32, bounds[3] - bounds[1] + 32) }
  const frame = restoreElements([{ type: 'frame', id: 'observation-frame',
    ...view, angle: 0, isDeleted: false }], null)[0]
  if (!frame || frame.type !== 'frame') throw new Error('Could not construct the observation viewport')
  const canvas = await exportToCanvas({ elements: restored, files: {}, exportingFrame: frame,
    appState: { viewBackgroundColor: '#ffffff', exportBackground: true, exportWithDarkMode: false },
    getDimensions: (width, height) => {
      const scale = max_size / Math.max(width, height)
      return { width: Math.max(1, Math.round(width * scale)), height: Math.max(1, Math.round(height * scale)), scale }
    } })
  return { image: canvas.toDataURL('image/png').split(',')[1], width: canvas.width, height: canvas.height,
    scene_bounds: view, pixels_per_unit: { x: canvas.width / view.width, y: canvas.height / view.height } }
}
