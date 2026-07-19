/** UI 风格: default=现有交易台, apple=苹果设计语言 */

export type UiStyle = 'default' | 'apple'

const KEY = 'tradeforge.uiStyle'
const EVENT = 'tradeforge:ui-style'

export function getUiStyle(): UiStyle {
  try {
    const v = localStorage.getItem(KEY)
    if (v === 'apple' || v === 'default') return v
  } catch { /* */ }
  // 兼容旧 theme 字段
  try {
    const raw = localStorage.getItem('tradeforge.settings')
    if (raw) {
      const s = JSON.parse(raw)
      if (s?.uiStyle === 'apple' || s?.uiStyle === 'default') return s.uiStyle
    }
  } catch { /* */ }
  return 'default'
}

export function setUiStyle(style: UiStyle): UiStyle {
  const next = style === 'apple' ? 'apple' : 'default'
  try {
    localStorage.setItem(KEY, next)
  } catch { /* */ }
  applyUiStyle(next)
  window.dispatchEvent(new CustomEvent(EVENT, { detail: next }))
  // 同步进 AppSettings,便于设置页展示
  try {
    const raw = localStorage.getItem('tradeforge.settings')
    const base = raw ? JSON.parse(raw) : {}
    localStorage.setItem('tradeforge.settings', JSON.stringify({ ...base, uiStyle: next }))
  } catch { /* */ }
  return next
}

export function applyUiStyle(style?: UiStyle): void {
  const s = style ?? getUiStyle()
  const root = document.documentElement
  root.setAttribute('data-ui-style', s)
  // 苹果浅色用 color-scheme 改善表单控件
  root.style.colorScheme = s === 'apple' ? 'light' : 'dark'
}

export function toggleUiStyle(): UiStyle {
  return setUiStyle(getUiStyle() === 'apple' ? 'default' : 'apple')
}

export function subscribeUiStyle(cb: (s: UiStyle) => void): () => void {
  const h = (e: Event) => cb((e as CustomEvent).detail || getUiStyle())
  window.addEventListener(EVENT, h)
  return () => window.removeEventListener(EVENT, h)
}

export const UI_STYLE_META: Record<UiStyle, { label: string; short: string }> = {
  default: { label: '默认', short: '默认' },
  apple: { label: '苹果', short: 'Apple' },
}
