export const COMMAND_CAPABILITY_GROUPS = Object.freeze([
  {
    title: '绘制与生成',
    summary: '基础图形、文字、路径、同心圆、复杂场景和结构化透视对象。',
    examples: ['画一个红色圆', '画同心圆，里面绿色外面蓝色', '画一辆有正常3D结构的汽车'],
  },
  {
    title: '选择与编辑',
    summary: '按对象、类型、颜色、区域、椭圆选区、套索和相似对象进行选择。',
    examples: ['选中红色圆', '椭圆框选中间这一片', '选择相似颜色的对象'],
  },
  {
    title: '变换与排版',
    summary: '移动、缩放、旋转、翻转、镜像、倾斜、层级、对齐和均匀分布。',
    examples: ['把房子移到左边', '水平翻转这个图形', '三个图形横向均匀分布'],
  },
  {
    title: '样式与滤镜',
    summary: '改色、填充、渐变、描边、透明度、混合模式和图片滤镜。',
    examples: ['填充成绿色', '加蓝到紫的渐变', '把图片像素化，给照片加噪点'],
  },
  {
    title: '对象组织',
    summary: '复制、成组、取消成组、布尔组合、裁剪、剪贴遮罩、锁定和隐藏。',
    examples: ['复制一个一样的', '从圆里减掉矩形', '锁定最后一个对象，隐藏蓝色圆'],
  },
  {
    title: '低延迟控制',
    summary: '撤销、重做、清空确认、停止当前绘图、重试和导出，不需要等待模型。',
    examples: ['撤销', '停止当前绘图', '导出当前画布为 PNG'],
  },
])

export const ANNOUNCEMENT_STORAGE_KEY = 'ai-draw-capability-notice-dismissed'

export function shouldShowCapabilityNotice(storage = window.localStorage) {
  return storage.getItem(ANNOUNCEMENT_STORAGE_KEY) !== '1'
}

export function rememberCapabilityNoticeDismissed(storage = window.localStorage) {
  storage.setItem(ANNOUNCEMENT_STORAGE_KEY, '1')
}
