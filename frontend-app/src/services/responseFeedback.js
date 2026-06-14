export function buildSuccessSpeech(response = {}) {
  if (response.code) return ''

  const elapsed = response.latency_ms
    ? `，耗时${Math.round(response.latency_ms / 100) / 10}秒`
    : ''
  return `${response.content || '绘图操作已完成'}${elapsed}`
}
