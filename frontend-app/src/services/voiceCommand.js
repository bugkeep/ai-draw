const LEADING_FILLERS = /^(?:(?:嗯+|呃+|额+|那个|然后|请|麻烦你|帮我|给我)\s*)+/u
const NOISE_ONLY = /^(?:你|我|他|她|它|的|了|啊|呀|哦|嗯|呃|额|然后|那个|好|好的)$/u
const EXACT_RECOGNITION_CORRECTIONS = new Map([
  ['话术', '画树'],
])

const RECOGNITION_CORRECTIONS = [
  [/^(?:带你来|但你来|带你|来)(?=画)/u, ''],
  [/(同心|同星|同兴|同型|套)(?:园|元)(?=$|\s|里|内|外|，|,)/gu, '$1圆'],
  [/有应该有/gu, '应该有'],
  [/有有/gu, '有'],
  [/一棵房子/gu, '一座房子'],
  [/一个人人旁边/gu, '一个人 人旁边'],
  [/人人旁边/gu, '人旁边'],
]

const EXPORT_FORMAT_RULES = [
  { format: 'svg', pattern: /\bsvg\b|矢量|可编辑/iu },
  { format: 'jpeg', pattern: /\bjpeg\b|\bjpg\b|照片/iu },
  { format: 'png', pattern: /\bpng\b|透明/iu },
]

const COMMAND_RULES = [
  { type: 'undo', pattern: /^(?:撤销|撤消|回退|返回上一步|上一步)(?:操作)?$/u },
  { type: 'redo', pattern: /^(?:重做|恢复|恢复上一步|再做一次)(?:操作)?$/u },
  { type: 'request_clear', pattern: /^(?:清空|清除|删除)(?:整个|全部|所有)?画布$/u },
  { type: 'request_clear', pattern: /^重新开始$/u },
  { type: 'confirm_clear', pattern: /^(?:确认|确定)(?:清空|清除)?$/u },
  { type: 'stop_current', pattern: /^(?:停止|取消|终止)(?:当前|正在执行的)?(?:绘图|任务|操作)$/u },
  { type: 'cancel', pattern: /^(?:取消|算了|不要|不用了|不要清空)$/u },
  { type: 'retry', pattern: /^(?:重试|再试一次|重新执行|再执行一次)$/u },
  {
    type: 'export_canvas',
    pattern: /^(?:导出|保存|下载|存成|另存为).{0,40}$/u,
  },
]

function detectExportFormat(text) {
  for (const rule of EXPORT_FORMAT_RULES) {
    if (rule.pattern.test(text)) return rule.format
  }
  return 'png'
}

export function normalizeVoiceTranscript(rawText) {
  let text = String(rawText || '')
    .normalize('NFKC')
    .replace(/[，。！？、,.!?;；:：]/gu, ' ')
    .replace(/\s+/gu, ' ')
    .trim()
    .replace(LEADING_FILLERS, '')
    .trim()

  text = EXACT_RECOGNITION_CORRECTIONS.get(text) || text
  for (const [pattern, replacement] of RECOGNITION_CORRECTIONS) {
    text = text.replace(pattern, replacement)
  }
  return text.replace(/\s+/gu, ' ').trim()
}

export function parseVoiceCommand(rawText) {
  const text = normalizeVoiceTranscript(rawText)
  if (!text || NOISE_ONLY.test(text)) return { type: 'empty', text: '' }

  for (const rule of COMMAND_RULES) {
    if (rule.pattern.test(text)) {
      if (rule.type === 'export_canvas') {
        return { type: rule.type, text, format: detectExportFormat(text) }
      }
      return { type: rule.type, text }
    }
  }

  return { type: 'draw', text }
}
