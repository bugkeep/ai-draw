import { useEffect, useState } from 'react'

export function activityPhase(value) {
  let text = ''
  if (value.phase === 'thinking') text = '正在安排绘图'
  if (value.phase === 'research') text = '正在检索并筛选绘图参考资料'
  if (value.phase === 'observing') text = '正在查看当前画布图片'
  if (value.phase === 'quality_review') text = '正在检查画面质量'
  if (value.phase === 'model_request') text = value.agent_id === 'main' ? '主 Agent 正在思考' : '子 Agent 正在准备画笔动作'
  if (['model_wait', 'model_retry'].includes(value.phase)) text = '模型连接正在限流，按顺序恢复请求'
  if (value.phase === 'agent_state') {
    const name = value.nickname || '子 Agent'
    if (value.result?.status === 'failed') text = `${name} 未完成，正在等待主 Agent 处理`
    else if (value.state === 'Shutdown') text = `${name} 已暂停，进度已保存`
    else if (value.result?.result_id) text = `${name} 已准备绘图，正在等待提交`
    else if (value.state === 'Running') text = `${name} 正在准备绘图${value.queued_inputs > 1 ? `，还有 ${value.queued_inputs - 1} 条输入排队` : ''}`
    else text = `${name} 已完成`
  }
  if (value.phase === 'drawing') text = `正在绘制 ${value.completed}/${value.total}`
  return text ? { taskId: value.task_id, text, retryAt: value.retry_at } : null
}

export default function TaskActivity({ task, phase, onStop }) {
  const [now, setNow] = useState(Date.now())
  const [stopping, setStopping] = useState(false)
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [])
  const current = phase?.taskId === task.id ? phase : null
  const elapsed = Math.max(0, Math.floor(now / 1000 - (task.created_at || now / 1000)))
  const remaining = Math.max(0, Math.ceil((current?.retryAt || 0) - now / 1000))
  async function stop() {
    if (stopping) return
    setStopping(true)
    try {
      await onStop()
    } catch {
      setStopping(false)
    }
  }
  return <div className="activity" role="status">
    <span className="pulse" />
    <div className="activity-description">
      <span>{stopping ? '正在停止，绘图进度将保留' : current?.text || (task.status === 'queued' ? '等待执行' : '正在绘图')}</span>
      <span className="activity-time">已用时 {Math.floor(elapsed / 60)}:{String(elapsed % 60).padStart(2, '0')}{remaining > 0 && !stopping ? ` · ${remaining} 秒后重试` : ''}</span>
    </div>
    <button type="button" onClick={stop} disabled={stopping}>{stopping ? '正在停止' : '停止'}</button>
  </div>
}
