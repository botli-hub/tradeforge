import { useEffect, useState } from 'react'
import PageErrorBoundary from './components/PageErrorBoundary'
import { ToastProvider } from './components/ui/Toast'
import MarketPage from './pages/MarketPage'
import StrategyPage from './pages/StrategyPage'
import BacktestPage from './pages/BacktestPage'
import OptionsPage from './pages/OptionsPage'
import OrdersPage from './pages/OrdersPage'
import PositionsPage from './pages/PositionsPage'
import HistoryPage from './pages/HistoryPage'
import SettingsPage from './pages/SettingsPage'
import StocksPage from './pages/StocksPage'
import LeapsMonitorPage from './pages/LeapsMonitorPage'
import Plan2032Page from './pages/Plan2032Page'
import WheelPage from './pages/WheelPage'
import { getAppMode, setAppMode, type AppMode } from './services/wheelProduct'

type PageKey = 'wheel' | 'market' | 'strategy' | 'backtest' | 'options' | 'orders' | 'positions' | 'history' | 'stocks' | 'leaps' | 'plan2032' | 'settings'

const TRADE_NAV: { key: PageKey; label: string }[] = [
  { key: 'wheel', label: 'Wheel' },
  { key: 'settings', label: '设置' },
]

const RESEARCH_NAV: { key: PageKey; label: string }[] = [
  { key: 'market', label: '行情' },
  { key: 'options', label: '期权' },
  { key: 'strategy', label: '策略' },
  { key: 'backtest', label: '回测' },
  { key: 'stocks', label: '股票池' },
  { key: 'history', label: '数据' },
  { key: 'leaps', label: 'LEAPS' },
  { key: 'orders', label: '订单' },
  { key: 'positions', label: '持仓' },
  { key: 'plan2032', label: '2032' },
  { key: 'settings', label: '设置' },
]

const MOBILE_TRADE_TABS: { key: PageKey; label: string; ico: string }[] = [
  { key: 'wheel', label: '今日', ico: '⌂' },
  { key: 'settings', label: '设置', ico: '⚙' },
]

const MOBILE_RESEARCH_TABS: { key: PageKey; label: string; ico: string }[] = [
  { key: 'market', label: '行情', ico: '📈' },
  { key: 'options', label: '期权', ico: '◉' },
  { key: 'strategy', label: '策略', ico: '⌘' },
  { key: 'settings', label: '设置', ico: '⚙' },
]

function App() {
  const [mode, setMode] = useState<AppMode>(() => getAppMode())
  const [currentPage, setCurrentPage] = useState<PageKey>(() =>
    getAppMode() === 'research' ? 'market' : 'wheel')

  useEffect(() => {
    const onMode = (e: Event) => {
      const m = (e as CustomEvent).detail as AppMode
      if (m === 'wheel' || m === 'research') setMode(m)
    }
    window.addEventListener('tradeforge:app-mode', onMode)
    return () => window.removeEventListener('tradeforge:app-mode', onMode)
  }, [])

  useEffect(() => {
    const valid: PageKey[] = [
      'wheel', 'market', 'strategy', 'backtest', 'options', 'orders',
      'positions', 'history', 'stocks', 'leaps', 'plan2032', 'settings',
    ]
    const onNav = (e: Event) => {
      const d = (e as CustomEvent).detail || {}
      const page = d.page as PageKey | undefined
      if (page && valid.includes(page)) {
        // 从 Wheel 跳设置时保持交易壳
        if (page === 'settings' || page === 'wheel') {
          setAppMode('wheel')
          setMode('wheel')
        }
        setCurrentPage(page)
        if (d.section) {
          queueMicrotask(() => {
            window.dispatchEvent(new CustomEvent('tradeforge:settings-section', {
              detail: { section: d.section },
            }))
          })
        }
      }
    }
    window.addEventListener('tradeforge:navigate', onNav)
    return () => window.removeEventListener('tradeforge:navigate', onNav)
  }, [])

  const nav = mode === 'wheel' ? TRADE_NAV : RESEARCH_NAV
  const mobileTabs = mode === 'wheel' ? MOBILE_TRADE_TABS : MOBILE_RESEARCH_TABS

  function switchMode(next: AppMode) {
    setAppMode(next)
    setMode(next)
    setCurrentPage(next === 'wheel' ? 'wheel' : 'market')
  }

  const pages: Record<PageKey, JSX.Element> = {
    wheel: <WheelPage />,
    market: <MarketPage />,
    strategy: <StrategyPage />,
    backtest: <BacktestPage />,
    options: <OptionsPage />,
    orders: <OrdersPage />,
    positions: <PositionsPage />,
    history: <HistoryPage />,
    stocks: <StocksPage />,
    leaps: <LeapsMonitorPage />,
    plan2032: <Plan2032Page />,
    settings: <SettingsPage />,
  }

  return (
    <ToastProvider>
      <div className="app-shell has-bottom-tabs">
        <nav className="nav" style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 4 }}>
          <div className="mode-seg" style={{ marginRight: 10 }}>
            <button type="button" className={mode === 'wheel' ? 'active' : ''} onClick={() => switchMode('wheel')}>
              交易
            </button>
            <button type="button" className={mode === 'research' ? 'active' : ''} onClick={() => switchMode('research')}>
              研究
            </button>
          </div>
          <div className="desktop-only" style={{ display: 'contents' }}>
            {nav.map(({ key, label }) => (
              <div
                key={key}
                className={`nav-item ${currentPage === key ? 'active' : ''}`}
                onClick={() => setCurrentPage(key)}
              >
                {label}
              </div>
            ))}
          </div>
          {mode === 'wheel' && (
            <span className="desktop-only" style={{ fontSize: 11, color: 'var(--text-secondary)', marginLeft: 'auto', paddingRight: 8 }}>
              状态机 · 机会 · 台账 · 富途成交后登记
            </span>
          )}
        </nav>

        {Object.entries(pages).map(([key, page]) => (
          <div key={key} style={{ display: currentPage === key ? 'block' : 'none' }} className="tab-panel">
            <PageErrorBoundary resetKey={key} pageName={key}>
              {page}
            </PageErrorBoundary>
          </div>
        ))}

        <nav className="bottom-tabs mobile-only" aria-label="主导航">
          {mobileTabs.map(t => (
            <button
              key={t.key}
              type="button"
              className={`bottom-tab ${currentPage === t.key ? 'active' : ''}`}
              onClick={() => setCurrentPage(t.key)}
            >
              <span className="ico" aria-hidden>{t.ico}</span>
              {t.label}
            </button>
          ))}
          {mode === 'wheel' && (
            <button
              type="button"
              className={`bottom-tab ${currentPage === 'plan2032' ? 'active' : ''}`}
              onClick={() => setCurrentPage('plan2032')}
            >
              <span className="ico" aria-hidden>◇</span>
              更多
            </button>
          )}
        </nav>
      </div>
    </ToastProvider>
  )
}

export default App
