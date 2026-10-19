import { useEffect, useRef, useState } from 'react'

export default function SessionListItem({ session, selected, editing, disabled, actionsDisabled, onSelect, onEdit, onCancel, onRename, onDelete }) {
  const [title, setTitle] = useState(session.title)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const inputRef = useRef(null)

  useEffect(() => {
    if (!editing) return
    setTitle(session.title)
    setError('')
    inputRef.current?.focus()
    inputRef.current?.select()
  }, [editing, session.title])

  async function save(event) {
    event.preventDefault()
    const next = title.trim()
    if (!next) { setError('请输入画布名称。'); return }
    if (saving) return
    setSaving(true)
    setError('')
    try {
      await onRename(session.id, next)
    } catch (cause) {
      setError(`保存名称失败：${cause.message}`)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="session-entry">
      <div className={`session-row ${selected ? 'selected' : ''}`}>
        <button type="button" className={`session-item ${selected ? 'selected' : ''}`} data-session-id={session.id}
          onClick={onSelect} disabled={disabled || saving} aria-current={selected ? 'page' : undefined} title={session.title}>
          <span className="session-icon" aria-hidden="true">◇</span><span className="session-title">{session.title}</span>
        </button>
        {!editing && <button type="button" className="session-rename-button" onClick={onEdit} disabled={disabled || actionsDisabled}
          aria-label="重命名" title="重命名">✎</button>}
        {!editing && <button type="button" className="session-delete-button" onClick={onDelete} disabled={disabled || actionsDisabled}
          aria-label="删除画布" title="删除画布">
          <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true">
            <path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13M10 10v7M14 10v7" />
          </svg>
        </button>}
      </div>
      {editing && <form className="session-rename-form" onSubmit={save} aria-label="重命名画布">
        <input ref={inputRef} aria-label="画布名称" value={title} maxLength={120} disabled={saving || disabled}
          onChange={(event) => { setTitle(event.target.value); setError('') }}
          onKeyDown={(event) => { if (event.key === 'Escape' && !saving) { event.preventDefault(); event.stopPropagation(); onCancel() } }} />
        <button type="submit" disabled={saving || disabled} aria-label="保存名称" title="保存名称">✓</button>
        <button type="button" disabled={saving || disabled} onClick={onCancel} aria-label="取消重命名" title="取消重命名">×</button>
      </form>}
      {editing && error && <div className="session-rename-error" role="alert">{error}</div>}
    </div>
  )
}
