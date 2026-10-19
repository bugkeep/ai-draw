import { useCallback, useEffect, useRef, useState } from 'react'
import { CaptureUpdateAction, DefaultSidebar, Excalidraw, exportToBlob } from '@excalidraw/excalidraw'
import { useNavigate } from 'react-router-dom'
import '@excalidraw/excalidraw/index.css'
import { eventsUrl, STUDIO_API_REVISION, studioApi } from './studioApi.js'
import SessionListItem from './SessionListItem.jsx'
import DeleteSessionDialog from './DeleteSessionDialog.jsx'

const CLIENT_ID = crypto.randomUUID()

function snapshotSignature(elements, selection) {
  return JSON.stringify({ elements, selection })
}

function selectedIds(appState) {
  return Object.entries(appState.selectedElementIds || {})
    .filter(([, isSelected]) => isSelected)
    .map(([id]) => id)
}

function taskLabel(task) {
  if (task?.status === 'queued') return '等待执行'
  if (task?.status === 'running') return '正在绘图'
  if (task?.status === 'failed') return '执行失败'
  if (task?.status === 'partial') return '部分完成'
  if (task?.status === 'cancelled') return '已停止'
  return '画布就绪'
}

export default function App() {
  const navigate = useNavigate()
  const [sessions, setSessions] = useState([])
  const [sessionId, setSessionId] = useState('')
  const [document, setDocument] = useState(null)
  const [messages, setMessages] = useState([])
  const [tasks, setTasks] = useState([])
  const [phase, setPhase] = useState(null)
  const [draft, setDraft] = useState('')
  const [error, setError] = useState('')
  const [providerReady, setProviderReady] = useState(false)
  const [apiReady, setApiReady] = useState(false)
  const [connected, setConnected] = useState(false)
  const [changingSession, setChangingSession] = useState(false)
  const [renamingSessionId, setRenamingSessionId] = useState('')
  const [deleteTarget, setDeleteTarget] = useState(null)
  const [pendingDeletion, setPendingDeletion] = useState('')
  const [sending, setSending] = useState(false)
  const [canvasKey, setCanvasKey] = useState('')
  const apiRef = useRef(null)
  const docRef = useRef(null)
  const sessionRef = useRef('')
  const wsRef = useRef(null)
  const reconnectRef = useRef(null)
  const lastSeqRef = useRef(0)
  const suppressRef = useRef(false)
  const suppressTimerRef = useRef(null)
  const lastSignatureRef = useRef('')
  const pendingSnapshotRef = useRef(null)
  const saveTimerRef = useRef(null)
  const savePromiseRef = useRef(null)
  const messageEndRef = useRef(null)
  const taskRef = useRef([])
  const generationRef = useRef(0)
  const loadRequestRef = useRef(0)
  const transitionRef = useRef(false)
  const mountedRef = useRef(true)
  const sendingRef = useRef(false)

  const activeTask = tasks.find((task) => task.status === 'running') || tasks.find((task) => task.status === 'queued')
  const busy = Boolean(activeTask)

  const setTaskList = useCallback((next) => {
    taskRef.current = next
    setTasks(next)
  }, [])

  const updateSessionTitle = useCallback((session) => {
    setSessions((current) => current.map((item) => item.id === session.id ? { ...item, title: session.title } : item))
    if (docRef.current?.id === session.document_id) {
      docRef.current = { ...docRef.current, title: session.title }
      setDocument(docRef.current)
    }
  }, [])

  const renameSession = useCallback(async (id, title) => {
    const renamed = await studioApi.renameSession(id, title)
    if (!mountedRef.current) return
    updateSessionTitle(renamed)
    setRenamingSessionId((current) => current === id ? '' : current)
  }, [updateSessionTitle])

  const applyDocument = useCallback((nextDocument, { showScene = true, focus = false } = {}) => {
    docRef.current = nextDocument
    setDocument(nextDocument)
    lastSignatureRef.current = snapshotSignature(nextDocument.elements, nextDocument.selection)
    if (showScene && apiRef.current) {
      suppressRef.current = true
      clearTimeout(suppressTimerRef.current)
      apiRef.current.updateScene({
        elements: nextDocument.elements,
        appState: { selectedElementIds: Object.fromEntries(nextDocument.selection.map((id) => [id, true])) },
        captureUpdate: CaptureUpdateAction.NEVER,
      })
      if (focus && nextDocument.elements.some((item) => !item.isDeleted)) {
        const generation = generationRef.current
        requestAnimationFrame(() => {
          if (generation !== generationRef.current || docRef.current?.id !== nextDocument.id) return
          apiRef.current?.scrollToContent(nextDocument.elements.filter((item) => !item.isDeleted),
            { fitToViewport: true, viewportZoomFactor: 0.75, animate: false })
        })
      }
      suppressTimerRef.current = setTimeout(() => { suppressRef.current = false }, 180)
    }
  }, [])

  const handleExcalidrawApi = useCallback((documentId, api) => {
    if (documentId !== docRef.current?.id) return
    apiRef.current = api
    if (api && docRef.current?.elements.some((item) => !item.isDeleted)) {
      const generation = generationRef.current
      setTimeout(() => {
        if (generation !== generationRef.current || apiRef.current !== api) return
        api.scrollToContent(docRef.current.elements.filter((item) => !item.isDeleted),
          { fitToViewport: true, viewportZoomFactor: 0.75, animate: false })
      }, 300)
    }
  }, [])

  const loadSession = useCallback(async (id, replaceCanvas = false) => {
    const requestId = ++loadRequestRef.current
    const data = await studioApi.getSession(id)
    if (!mountedRef.current || requestId !== loadRequestRef.current) return
    generationRef.current += 1
    wsRef.current?.close()
    clearTimeout(reconnectRef.current)
    clearTimeout(saveTimerRef.current)
    pendingSnapshotRef.current = null
    sessionRef.current = id
    setSessionId(id)
    setMessages(data.messages)
    setTaskList(data.tasks)
    lastSeqRef.current = data.event_seq || 0
    setDraft('')
    setRenamingSessionId('')
    if (replaceCanvas) {
      apiRef.current = null
      setCanvasKey(id)
    }
    applyDocument(data.document, { showScene: !replaceCanvas })
    localStorage.setItem('draw-agent-session', id)
    return data
  }, [applyDocument, setTaskList])

  useEffect(() => {
    let cancelled = false
    async function start() {
      try {
        const [health, available] = await Promise.all([studioApi.health(), studioApi.listSessions()])
        if (cancelled) return
        setProviderReady(health.provider_configured)
        setApiReady(health.api_revision === STUDIO_API_REVISION)
        setSessions(available)
        const remembered = localStorage.getItem('draw-agent-session')
        const chosen = available.find((item) => item.id === remembered) || available[0]
        if (chosen) await loadSession(chosen.id, true)
        else {
          const created = await studioApi.createSession('我的画布')
          if (cancelled) return
          setSessions([{ id: created.id, document_id: created.document.id, title: created.title }, ...available])
          await loadSession(created.id, true)
        }
      } catch (cause) {
        if (!cancelled) setError(cause.message)
      }
    }
    start()
    return () => { cancelled = true }
  }, [loadSession])

  useEffect(() => {
    if (apiReady) return
    let cancelled = false
    const timer = setInterval(async () => {
      try {
        const health = await studioApi.health()
        if (!cancelled) {
          setApiReady(health.api_revision === STUDIO_API_REVISION)
          setProviderReady(health.provider_configured)
        }
      } catch (cause) {
        if (!cancelled) setError(cause.message)
      }
    }, 2500)
    return () => { cancelled = true; clearInterval(timer) }
  }, [apiReady])

  const recoverDeletedSession = useCallback(async (id) => {
    setSessions((current) => current.filter((item) => item.id !== id))
    setDeleteTarget((current) => current?.id === id ? null : current)
    if (sessionRef.current !== id) return
    generationRef.current += 1
    wsRef.current?.close()
    clearTimeout(reconnectRef.current)
    clearTimeout(saveTimerRef.current)
    pendingSnapshotRef.current = null
    docRef.current = null
    sessionRef.current = ''
    apiRef.current = null
    setDocument(null)
    setSessionId('')
    setMessages([])
    setTaskList([])
    setPhase(null)
    setDraft('')
    localStorage.removeItem('draw-agent-session')
    const available = await studioApi.listSessions()
    if (!available.length) {
      const created = await studioApi.createSession('我的画布')
      available.push({ id: created.id, document_id: created.document.id, title: created.title })
    }
    if (!mountedRef.current) return
    setSessions(available)
    await loadSession(available[0].id, true)
    setError('')
  }, [loadSession, setTaskList])

  useEffect(() => {
    if (!pendingDeletion || changingSession || transitionRef.current) return
    transitionRef.current = true
    setChangingSession(true)
    void recoverDeletedSession(pendingDeletion).catch((cause) => {
      if (mountedRef.current) setError(cause.message)
    }).finally(() => {
      transitionRef.current = false
      if (mountedRef.current) { setChangingSession(false); setPendingDeletion('') }
    })
  }, [pendingDeletion, changingSession, recoverDeletedSession])

  const flushSnapshot = useCallback(async () => {
    clearTimeout(saveTimerRef.current)
    if (savePromiseRef.current) {
      await savePromiseRef.current
      if (pendingSnapshotRef.current) return flushSnapshot()
      return
    }
    const snapshot = pendingSnapshotRef.current
    if (!snapshot || !docRef.current) return
    pendingSnapshotRef.current = null
    const current = docRef.current
    if (snapshot.documentId !== current.id) return
    const generation = generationRef.current
    const request = studioApi.saveScene(current.id, {
      version: current.version,
      elements: snapshot.elements,
      selection: snapshot.selection,
      client_id: CLIENT_ID,
    })
    savePromiseRef.current = request
    try {
      const saved = await request
      if (!mountedRef.current || generation !== generationRef.current || docRef.current?.id !== current.id) return
      docRef.current = saved
      setDocument(saved)
      lastSignatureRef.current = snapshotSignature(saved.elements, saved.selection)
    } catch (cause) {
      if (!mountedRef.current || generation !== generationRef.current) throw cause
      setError(`保存画布失败：${cause.message}`)
      pendingSnapshotRef.current = null
      if (cause.status === 409) {
        const latest = await studioApi.getSession(sessionRef.current)
        if (generation === generationRef.current) applyDocument(latest.document)
      }
      throw cause
    } finally {
      savePromiseRef.current = null
    }
    if (pendingSnapshotRef.current) await flushSnapshot()
  }, [applyDocument])

  const deleteSession = useCallback(async (id) => {
    if (transitionRef.current) throw new Error('正在切换画布，请稍后重试。')
    transitionRef.current = true
    setChangingSession(true)
    try {
      if (sessionRef.current === id) await flushSnapshot()
      await studioApi.deleteSession(id)
      await recoverDeletedSession(id)
    } finally {
      transitionRef.current = false
      if (mountedRef.current) setChangingSession(false)
    }
  }, [flushSnapshot, recoverDeletedSession])

  const handleCanvasChange = useCallback((documentId, elements, appState) => {
    if (documentId !== docRef.current?.id || suppressRef.current || transitionRef.current) return
    const snapshot = { documentId: docRef.current.id, elements: JSON.parse(JSON.stringify(elements)), selection: selectedIds(appState) }
    const signature = snapshotSignature(snapshot.elements, snapshot.selection)
    if (signature === lastSignatureRef.current) return
    pendingSnapshotRef.current = snapshot
    clearTimeout(saveTimerRef.current)
    saveTimerRef.current = setTimeout(() => { void flushSnapshot().catch(() => {}) }, 260)
  }, [flushSnapshot])

  const onExcalidrawApi = useCallback((api) => handleExcalidrawApi(document?.id, api), [document?.id, handleExcalidrawApi])
  const onCanvasChange = useCallback((elements, appState) => handleCanvasChange(document?.id, elements, appState), [document?.id, handleCanvasChange])

  const showStroke = useCallback((element) => {
    const current = docRef.current
    if (!current || !apiRef.current) return
    suppressRef.current = true
    clearTimeout(suppressTimerRef.current)
    apiRef.current.updateScene({
      elements: [...current.elements, element],
      captureUpdate: CaptureUpdateAction.NEVER,
    })
    suppressTimerRef.current = setTimeout(() => { suppressRef.current = false }, 180)
  }, [])

  const handleEvent = useCallback((documentId, event) => {
    if (docRef.current?.id !== documentId || event.document_id !== documentId) return
    if (event.seq <= lastSeqRef.current) return
    lastSeqRef.current = event.seq
    const value = event.payload
    if (event.type === 'scene') {
      if (value.source === 'manual' && value.client_id === CLIENT_ID) return
      if (value.source !== 'manual') {
        clearTimeout(saveTimerRef.current)
        pendingSnapshotRef.current = null
      }
      applyDocument(value.document, { focus: value.source === 'agent' })
    } else if (event.type === 'stroke_progress') {
      showStroke(value.element)
    } else if (event.type === 'session') {
      updateSessionTitle(value)
    } else if (event.type === 'session_deleted') {
      setPendingDeletion(value.id)
    } else if (event.type === 'message') {
      setMessages((current) => current.some((item) => item.id === value.id) ? current : [...current, value])
    } else if (event.type === 'task') {
      const current = taskRef.current
      const prior = current.find((item) => item.id === value.task_id)
      const next = [{ ...prior, id: value.task_id, status: value.status,
        summary: value.summary || prior?.summary || '', error: value.error || '' },
      ...current.filter((item) => item.id !== value.task_id)].slice(0, 20)
      setTaskList(next)
      if (value.status === 'failed') setError(value.error)
      if (['completed', 'partial', 'failed', 'cancelled'].includes(value.status)) {
        setPhase((current) => current?.taskId === value.task_id ? null : current)
      }
    } else if (event.type === 'phase') {
      let text = ''
      if (value.phase === 'planned') text = value.delegations ? `正在安排 ${value.delegations} 个子 Agent` : '正在准备绘图'
      if (value.phase === 'child_ready') text = `子 Agent ${value.child_id} 已准备动作`
      if (value.phase === 'recovering') text = `正在重新准备 ${value.count} 个绘图部分`
      if (value.phase === 'drawing') text = `正在绘制 ${value.completed}/${value.total}`
      if (text) setPhase({ taskId: value.task_id, text })
    }
  }, [applyDocument, setTaskList, showStroke, updateSessionTitle])

  useEffect(() => {
    if (!document?.id) return
    const documentId = document.id
    const generation = generationRef.current
    let closed = false
    let socket
    let reconnectTimer
    function connect() {
      if (closed || generation !== generationRef.current) return
      const ws = new WebSocket(eventsUrl(documentId, lastSeqRef.current))
      socket = ws
      wsRef.current = ws
      ws.onopen = () => { if (!closed && generation === generationRef.current) setConnected(true) }
      ws.onmessage = (message) => {
        if (!closed && generation === generationRef.current) handleEvent(documentId, JSON.parse(message.data))
      }
      ws.onclose = () => {
        if (closed || generation !== generationRef.current) return
        setConnected(false)
        const targetSession = sessionRef.current
        void studioApi.getSession(targetSession).catch((cause) => {
          if (cause.status === 404 && !closed && generation === generationRef.current) setPendingDeletion(targetSession)
        })
        reconnectTimer = setTimeout(connect, 1200)
        reconnectRef.current = reconnectTimer
      }
      ws.onerror = () => { ws.close() }
    }
    connect()
    return () => {
      closed = true
      clearTimeout(reconnectTimer)
      socket?.close()
      setConnected(false)
    }
  }, [document?.id, handleEvent])

  useEffect(() => () => {
    mountedRef.current = false
    generationRef.current += 1
    clearTimeout(saveTimerRef.current)
    clearTimeout(suppressTimerRef.current)
    clearTimeout(reconnectRef.current)
    wsRef.current?.close()
  }, [])

  useEffect(() => {
    messageEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [messages.length])

  const send = useCallback(async (event) => {
    event.preventDefault()
    const text = draft.trim()
    if (!apiReady || !text || !sessionRef.current || transitionRef.current || sendingRef.current) return
    const targetSession = sessionRef.current
    const generation = generationRef.current
    sendingRef.current = true
    setSending(true)
    setError('')
    try {
      await flushSnapshot()
      if (generation !== generationRef.current || transitionRef.current) return
      const task = await studioApi.sendMessage(targetSession, text)
      if (!mountedRef.current || generation !== generationRef.current || targetSession !== sessionRef.current) return
      setDraft('')
      setMessages((current) => [...current, { id: `local-${task.id}`, role: 'user', content: text, task_id: task.id }])
      const prior = taskRef.current.find((item) => item.id === task.id)
      setTaskList([{ ...task, ...prior }, ...taskRef.current.filter((item) => item.id !== task.id)].slice(0, 20))
    } catch (cause) {
      if (mountedRef.current && generation === generationRef.current) setError(cause.message)
    } finally {
      sendingRef.current = false
      if (mountedRef.current) setSending(false)
    }
  }, [apiReady, draft, flushSnapshot, setTaskList])

  const createSession = useCallback(async () => {
    if (!docRef.current || transitionRef.current) return
    transitionRef.current = true
    setChangingSession(true)
    try {
      await flushSnapshot()
      const created = await studioApi.createSession('新的画布')
      setSessions((current) => [{ id: created.id, document_id: created.document.id, title: created.title }, ...current])
      await loadSession(created.id, true)
      setRenamingSessionId(created.id)
      setPhase(null)
      setError('')
    } catch (cause) {
      setError(cause.message)
    } finally {
      transitionRef.current = false
      setChangingSession(false)
    }
  }, [changingSession, flushSnapshot, loadSession])

  const switchSession = useCallback(async (id) => {
    if (id === sessionRef.current || transitionRef.current) return
    transitionRef.current = true
    setChangingSession(true)
    try {
      await flushSnapshot()
      await loadSession(id, true)
      setPhase(null)
      setError('')
    } catch (cause) {
      setError(cause.message)
    } finally {
      transitionRef.current = false
      setChangingSession(false)
    }
  }, [changingSession, flushSnapshot, loadSession])

  const history = useCallback(async (direction) => {
    if (!docRef.current || activeTask || transitionRef.current) return
    const documentId = docRef.current.id
    const generation = generationRef.current
    try {
      await flushSnapshot()
      if (generation !== generationRef.current) return
      const result = await studioApi.history(documentId, direction)
      if (mountedRef.current && generation === generationRef.current) applyDocument(result)
    } catch (cause) {
      if (mountedRef.current && generation === generationRef.current) setError(cause.message)
    }
  }, [activeTask, applyDocument, flushSnapshot])

  const openSettings = useCallback(async () => {
    if (transitionRef.current) return
    transitionRef.current = true
    setChangingSession(true)
    try {
      await flushSnapshot()
      navigate('/settings')
    } catch (cause) {
      setError(cause.message)
    } finally {
      transitionRef.current = false
      if (mountedRef.current) setChangingSession(false)
    }
  }, [flushSnapshot, navigate])

  const cancelActiveTask = useCallback(async () => {
    if (!activeTask || transitionRef.current) return
    const generation = generationRef.current
    try {
      await studioApi.cancelTask(activeTask.id)
    } catch (cause) {
      if (mountedRef.current && generation === generationRef.current) setError(cause.message)
    }
  }, [activeTask])

  useEffect(() => {
    function onKeyDown(event) {
      if (!(event.ctrlKey || event.metaKey) || !['z', 'y'].includes(event.key.toLowerCase())) return
      const target = event.target
      if (target instanceof HTMLElement && (target.closest('input, textarea') || target.isContentEditable)) return
      event.preventDefault()
      event.stopPropagation()
      void history(event.key.toLowerCase() === 'y' || event.shiftKey ? 'redo' : 'undo')
    }
    window.addEventListener('keydown', onKeyDown, true)
    return () => window.removeEventListener('keydown', onKeyDown, true)
  }, [history])

  const exportPng = useCallback(async () => {
    if (!apiRef.current) return
    try {
      const blob = await exportToBlob({
        elements: apiRef.current.getSceneElements(),
        appState: apiRef.current.getAppState(),
        files: apiRef.current.getFiles(),
        mimeType: 'image/png',
      })
      const url = URL.createObjectURL(blob)
      const link = window.document.createElement('a')
      link.href = url
      link.download = 'draw-agent-canvas.png'
      link.click()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch (cause) {
      setError(cause.message)
    }
  }, [])

  return (
    <div className="studio">
      <header className="topbar">
        <div className="brand"><span className="brand-mark">✦</span><span>Draw Agent</span><small>Studio</small></div>
        <div className="top-actions">
          <span className={`connection ${connected ? 'online' : ''}`}>{connected ? '已连接' : '连接中'}</span>
          <button type="button" onClick={() => history('undo')} disabled={!document || busy} title="撤销 Ctrl+Z">↶ 撤销</button>
          <button type="button" onClick={() => history('redo')} disabled={!document || busy} title="重做 Ctrl+Y">↷ 重做</button>
          <button type="button" onClick={exportPng} disabled={!document}>导出 PNG</button>
          <button type="button" onClick={openSettings} disabled={changingSession} aria-label="模型设置">⚙ 设置</button>
        </div>
      </header>
      {!apiReady && <div className="backend-notice" role="status">正在核查服务版本。若已更新项目代码，请重新启动 Draw Agent 后端服务；连接恢复后可继续操作。</div>}
      <main className="workspace">
        <nav className="sessions" aria-label="画布会话">
          <div className="section-heading"><span>画布</span><button type="button" onClick={createSession} disabled={!apiReady || !document || changingSession}>＋ 新建</button></div>
          <div className="session-list">
            {sessions.map((item) => <SessionListItem key={item.id} session={item} selected={sessionId === item.id}
              editing={renamingSessionId === item.id} disabled={changingSession} actionsDisabled={!apiReady}
              onSelect={() => switchSession(item.id)} onEdit={() => setRenamingSessionId(item.id)}
              onCancel={() => setRenamingSessionId('')} onRename={renameSession} onDelete={() => setDeleteTarget(item)} />)}
          </div>
          <div className="session-hint">每段对话保留同一张画布。可在画布上直接选择和修改图形。</div>
        </nav>
        <section className="canvas-panel" aria-label="Excalidraw 画布">
          <div className="canvas-heading"><span className="canvas-title" title={document?.title}>{document?.title || '画布'}</span>
            <span className="revision">版本 {document?.version ?? 0} · {document?.elements.filter((item) => !item.isDeleted).length ?? 0} 个对象</span></div>
          <div className="editor-host">
            {document && <Excalidraw key={canvasKey} excalidrawAPI={onExcalidrawApi}
              initialData={{ elements: document.elements, appState: {
                selectedElementIds: Object.fromEntries(document.selection.map((id) => [id, true])),
                viewBackgroundColor: '#ffffff',
              } }}
              onChange={onCanvasChange}
              onPaste={(data) => {
                if (Object.keys(data.files || {}).length > 0 || data.mixedContent?.some((item) => item.type === 'imageUrl')) {
                  setError('当前画布支持原生绘图对象；请使用画笔、图形或文字工具。')
                  return true
                }
                return false
              }}
              viewModeEnabled={busy || changingSession}
              UIOptions={{ tools: { image: false }, canvasActions: {
                export: false, loadScene: false, saveToActiveFile: false, saveAsImage: false,
              } }}
            >
              <DefaultSidebar.Trigger style={{ display: 'none' }} />
            </Excalidraw>}
          </div>
          <div className="canvas-footnote">Agent 创建的每一步都会显示在画布上 · 你也可以用 Excalidraw 工具继续编辑</div>
        </section>
        <aside className="chat" aria-label="绘图对话">
          <div className="chat-heading"><div><strong>绘图 Agent</strong><span>通过对话逐步完成作品</span></div>
            <span className={`agent-status ${busy ? 'active' : ''}`}>{taskLabel(activeTask || tasks[0])}</span></div>
          <div className="conversation">
            {messages.length === 0 && <div className="welcome">
              <div className="welcome-icon">✧</div><h2>从一个想法开始</h2>
              <p>描述想画的内容。完成后继续说“选中它”“移到左边”“改成蓝色”，Agent 会在同一张画布上接着编辑。</p>
              <button type="button" onClick={() => setDraft('用画笔画一张笑脸，展示作画过程')}>试试画一张笑脸 →</button>
            </div>}
            {messages.map((item) => <div key={item.id} className={`bubble ${item.role}`}>
              <span className="bubble-role">{item.role === 'user' ? '你' : 'Agent'}</span>
              <p>{item.content}</p>
            </div>)}
            {busy && <div className="activity"><span className="pulse" />{phase?.taskId === activeTask.id ? phase.text : taskLabel(activeTask)}
              <button type="button" onClick={cancelActiveTask}>停止</button></div>}
            {error && <div className="error-message" role="alert">{error}</div>}
            <div ref={messageEndRef} />
          </div>
          <form className="composer" onSubmit={send}>
            <textarea aria-label="绘图指令" placeholder="描述你想创建或修改的内容…" value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); event.currentTarget.form.requestSubmit() } }} />
            <div className="composer-bottom"><span>Enter 发送 · Shift+Enter 换行</span><button type="submit" disabled={!apiReady || !draft.trim() || !document || changingSession || sending}>发送 ↑</button></div>
          </form>
          {!providerReady && <div className="config-note">请前往<button type="button" onClick={openSettings}>模型设置</button>配置模型连接。</div>}
        </aside>
      </main>
      {deleteTarget && <DeleteSessionDialog key={deleteTarget.id} session={deleteTarget}
        blocked={deleteTarget.id === sessionId && busy} onCancel={() => setDeleteTarget(null)} onDelete={deleteSession} />}
    </div>
  )
}
