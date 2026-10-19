import { useEffect, useRef, useState } from 'react'
import { exportToBlob, exportToSvg, serializeAsJSON } from '@excalidraw/excalidraw'

const FORMATS = {
  png: { label: 'PNG', mime: 'image/png' },
  jpg: { label: 'JPG', mime: 'image/jpeg' },
  webp: { label: 'WebP', mime: 'image/webp' },
  svg: { label: 'SVG', mime: 'image/svg+xml' },
  excalidraw: { label: 'Excalidraw（可继续编辑）', mime: 'application/json' },
}

export default function ExportDialog({ snapshot, onClose }) {
  const dialogRef = useRef(null)
  const [format, setFormat] = useState('png')
  const [filename, setFilename] = useState(snapshot.title || '我的作品')
  const [scale, setScale] = useState(2)
  const [transparent, setTransparent] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => {
    const dialog = dialogRef.current
    dialog.showModal()
    return () => dialog.close()
  }, [])

  async function download(event) {
    event.preventDefault()
    if (saving) return
    setSaving(true)
    setError('')
    try {
      const appState = { ...snapshot.appState, exportBackground: format === 'jpg' || !transparent,
        exportWithDarkMode: false, exportEmbedScene: false }
      const options = { elements: snapshot.elements, appState, files: snapshot.files }
      let blob
      if (format === 'svg') {
        const svg = await exportToSvg(options)
        blob = new Blob([new XMLSerializer().serializeToString(svg)], { type: FORMATS.svg.mime })
      } else if (format === 'excalidraw') {
        blob = new Blob([serializeAsJSON(snapshot.elements, appState, snapshot.files, 'local')], { type: FORMATS.excalidraw.mime })
      } else {
        blob = await exportToBlob({ ...options, mimeType: FORMATS[format].mime, quality: 0.95,
          getDimensions: (width, height) => ({ width: width * scale, height: height * scale, scale }) })
        if (blob.type !== FORMATS[format].mime) throw new Error('当前浏览器不支持此图片格式，请选择其他格式。')
      }
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      const name = filename.trim().replace(/[<>:"/\\|?*\u0000-\u001f]/g, '_') || '我的作品'
      link.download = `${name}.${format}`
      link.click()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
      onClose()
    } catch (cause) {
      setError(cause.message)
      setSaving(false)
    }
  }

  return <dialog ref={dialogRef} className="export-dialog" aria-labelledby="export-title"
    onCancel={(event) => { event.preventDefault(); if (!saving) onClose() }}>
    <form onSubmit={download}>
      <h2 id="export-title">导出作品</h2>
      <label>文件名称<input value={filename} maxLength={120} onChange={(event) => setFilename(event.target.value)} disabled={saving} /></label>
      <label>文件格式<select value={format} onChange={(event) => setFormat(event.target.value)} disabled={saving}>
        {Object.entries(FORMATS).map(([value, item]) => <option key={value} value={value}>{item.label}</option>)}
      </select></label>
      {['png', 'jpg', 'webp'].includes(format) && <label>图片清晰度<select value={scale} onChange={(event) => setScale(Number(event.target.value))} disabled={saving}>
        <option value={1}>标准 · 1 倍</option><option value={2}>高清 · 2 倍</option><option value={4}>超清 · 4 倍</option>
      </select></label>}
      {format !== 'excalidraw' && <label className="export-checkbox"><input type="checkbox" checked={format !== 'jpg' && transparent}
        onChange={(event) => setTransparent(event.target.checked)} disabled={saving || format === 'jpg'} />透明背景</label>}
      {format === 'jpg' && <p>JPG 使用画布背景颜色。</p>}
      {format === 'svg' && <p>矢量格式，适合放大与排版。</p>}
      {format === 'excalidraw' && <p>保留图形、文字与画笔对象，可在 Excalidraw 中继续编辑。</p>}
      {error && <p role="alert" className="dialog-error">{error}</p>}
      <div className="dialog-actions"><button type="button" onClick={onClose} disabled={saving}>取消</button>
        <button type="submit" disabled={saving}>{saving ? '正在导出…' : '下载文件'}</button></div>
    </form>
  </dialog>
}
