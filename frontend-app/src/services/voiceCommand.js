const LEADING_FILLERS = /^(?:(?:嗯+|呃+|额+|那个|然后|请|麻烦你|帮我|给我)\s*)+/u
const NOISE_ONLY = /^(?:你|我|他|她|它|的|了|啊|呀|哦|嗯|呃|额|然后|那个|好|好的)$/u

const COMMAND_RULES = [
  { type: 'undo', pattern: /^(?:撤销|撤消|回退|返回上一步|上一步)(?:操作)?$/u },
  { type: 'redo', pattern: /^(?:重做|恢复|恢复上一步|再做一次)(?:操作)?$/u },
  { type: 'request_clear', pattern: /^(?:清空|清除|删除)(?:整个|全部|所有)?画布$/u },
  { type: 'request_clear', pattern: /^重新开始$/u },
  { type: 'confirm_clear', pattern: /^(?:确认|确定)(?:清空|清除)?$/u },
  { type: 'cancel', pattern: /^(?:取消|算了|不要|不用了|不要清空)$/u },
  { type: 'retry', pattern: /^(?:重试|再试一次|重新执行|再执行一次)$/u },
]

export function normalizeVoiceTranscript(rawText) {
  return String(rawText || '')
    .normalize('NFKC')
    .replace(/[，。！？、,.!?;；:：]/gu, ' ')
    .replace(/\s+/gu, ' ')
    .trim()
    .replace(LEADING_FILLERS, '')
    .trim()
}

export function parseVoiceCommand(rawText) {
  const text = normalizeVoiceTranscript(rawText)
  if (!text || NOISE_ONLY.test(text)) return { type: 'empty', text: '' }

  for (const rule of COMMAND_RULES) {
    if (rule.pattern.test(text)) return { type: rule.type, text }
  }

  return { type: 'draw', text }
}
