/** In-play wheel statuses. Matches WheelCycleStatus; IDLE and CLOSED are not drawn as card dots. */
export const OPEN_WHEEL_STATUSES = ['CSP_OPEN', 'HOLDING', 'CC_OPEN'] as const

const OPEN_WHEEL_STATUS_SET = new Set<string>(OPEN_WHEEL_STATUSES)

/** Symbol-card dots: open wheels only, original order preserved. */
export function visibleSymbolCycleDots<T extends { status: string }>(cycles: T[]): T[] {
  return cycles.filter(c => OPEN_WHEEL_STATUS_SET.has(c.status))
}
