/** UI 风格: default | apple | cyber */

export type UiStyle = 'default' | 'apple' | 'cyber'

const KEY = 'tradeforge.uiStyle'
const EVENT = 'tradeforge:ui-style'
const ALL: UiStyle[] = ['default', 'apple', 'cyber']

export function isUiStyle(v: unknown): v is UiStyle {
  return v === 'default' || v === 'apple' || v === 'cyber'
}

export function getUiStyle(): UiStyle {
  try {
    const v = localStorage.getItem(KEY)
    if (isUiStyle(v)) return v
  } catch { /* */ }
  try {
    const raw = localStorage.getItem('tradeforge.settings')
    if (raw) {
      const s = JSON.parse(raw)
      if (isUiStyle(s?.uiStyle)) return s.uiStyle
    }
  } catch { /* */ }
  return 'default'
}

export function setUiStyle(style: UiStyle | string): UiStyle {
  const next: UiStyle = isUiStyle(style) ? style : 'default'
  try {
    localStorage.setItem(KEY, next)
  } catch { /* */ }
  applyUiStyle(next)
  window.dispatchEvent(new CustomEvent(EVENT, { detail: next }))
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
  root.style.colorScheme = s === 'apple' ? 'light' : 'dark'
}

/** 在三套风格间轮换 */
export function cycleUiStyle(): UiStyle {
  const i = ALL.indexOf(getUiStyle())
  return setUiStyle(ALL[(i + 1) % ALL.length])
}

export function toggleUiStyle(): UiStyle {
  return cycleUiStyle()
}

export function subscribeUiStyle(cb: (s: UiStyle) => void): () => void {
  const h = (e: Event) => cb((e as CustomEvent).detail || getUiStyle())
  window.addEventListener(EVENT, h)
  return () => window.removeEventListener(EVENT, h)
}

export const UI_STYLE_META: Record<UiStyle, { label: string; short: string; title: string }> = {
  default: { label: '默认', short: '默认', title: '默认交易台风格' },
  apple: { label: '苹果', short: 'Apple', title: '苹果设计风格' },
  cyber: { label: '赛博', short: '赛博', title: '赛博朋克风格' },
}

export const UI_STYLE_OPTIONS = ALL
