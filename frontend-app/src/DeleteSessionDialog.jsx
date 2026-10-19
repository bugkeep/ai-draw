import { useEffect, useRef, useState } from 'react'

export default function DeleteSessionDialog({ session, blocked, onCancel, onDelete }) {
  const dialogRef = useRef(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    const dialog = dialogRef.current
    dialog.showModal()
    return () => dialog.close()
  }, [])

  async function confirm(event) {
    event.preventDefault()
    if (saving || blocked) return
    setSaving(true)
    setError('')
    try {
      await onDelete(session.id)
    } catch (cause) {
      setError(cause.message)
      setSaving(false)
    }
  }

  return <dialog ref={dialogRef} className="delete-session-dialog" aria-labelledby="delete-session-title"
    onCancel={(event) => { event.preventDefault(); if (!saving) onCancel() }}>
    <form onSubmit={confirm}>
      <h2 id="delete-session-title">删除画布会话</h2>
      <p>确认删除“<strong>{session.title}</strong>”？画布、对话、绘图任务和工具记录将永久删除，此操作无法撤销。</p>
      {blocked && <p role="status">此画布有正在运行或排队的任务，请停止任务后再删除。</p>}
      {error && <p className="dialog-error" role="alert">{error}</p>}
      <div className="dialog-actions">
        <button type="button" autoFocus disabled={saving} onClick={onCancel}>取消</button>
        <button type="submit" className="danger-button" disabled={saving || blocked}>{saving ? '正在删除…' : '确认删除'}</button>
      </div>
    </form>
  </dialog>
}
