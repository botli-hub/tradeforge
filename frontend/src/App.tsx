import { useState } from 'react'
import PageErrorBoundary from './components/PageErrorBoundary'
import MarketPage from './pages/MarketPage'
import StrategyPage from './pages/StrategyPage'
import BacktestPage from './pages/BacktestPage'
import OptionsPage from './pages/OptionsPage'
import OrdersPage from './pages/OrdersPage'
import PositionsPage from './pages/PositionsPage'
import HistoryPage from './pages/HistoryPage'
import SettingsPage from './pages/SettingsPage'
import StocksPage from './pages/StocksPage'
import Plan2032Page from './pages/Plan2032Page'

type PageKey = 'market' | 'strategy' | 'backtest' | 'options' | 'orders' | 'positions' | 'history' | 'stocks' | 'plan2032' | 'settings'

const NAV_ITEMS: { key: PageKey; label: string }[] = [
  { key: 'plan2032', label: '2032Plan' },
  { key: 'market', label: '行情' },
  { key: 'strategy', label: '策略' },
  { key: 'backtest', label: '回测' },
  { key: 'options', label: '期权' },
  { key: 'orders', label: '订单' },
  { key: 'positions', label: '持仓' },
  { key: 'history', label: '数据' },
  { key: 'stocks', label: '股票池' },
  { key: 'settings', label: '设置' },
]

function App() {
  const [currentPage, setCurrentPage] = useState<PageKey>('plan2032')

  const pages: Record<PageKey, JSX.Element> = {
    market: <MarketPage />,
    strategy: <StrategyPage />,
    backtest: <BacktestPage />,
    options: <OptionsPage />,
    orders: <OrdersPage />,
    positions: <PositionsPage />,
    history: <HistoryPage />,
    stocks: <StocksPage />,
    plan2032: <Plan2032Page />,
    settings: <SettingsPage />,
  }

  return (
    <div>
      <nav className="nav">
        {NAV_ITEMS.map(({ key, label }) => (
          <div
            key={key}
            className={`nav-item ${currentPage === key ? 'active' : ''}`}
            onClick={() => setCurrentPage(key)}
          >
            {label}
          </div>
        ))}
      </nav>

      {Object.entries(pages).map(([key, page]) => (
        <div key={key} style={{ display: currentPage === key ? 'block' : 'none' }}>
          <PageErrorBoundary resetKey={key} pageName={key}>
            {page}
          </PageErrorBoundary>
        </div>
      ))}
    </div>
  )
}

export default App
