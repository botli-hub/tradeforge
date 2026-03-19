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

type PageKey = 'market' | 'strategy' | 'backtest' | 'options' | 'orders' | 'positions' | 'history' | 'settings'

function App() {
  const [currentPage, setCurrentPage] = useState<PageKey>('market')

  const pages: Record<PageKey, JSX.Element> = {
    market: <MarketPage />,
    strategy: <StrategyPage />,
    backtest: <BacktestPage />,
    options: <OptionsPage />,
    orders: <OrdersPage />,
    positions: <PositionsPage />,
    history: <HistoryPage />,
    settings: <SettingsPage />
  }

  return (
    <div>
      <div className="nav">
        <div className={`nav-item ${currentPage === 'market' ? 'active' : ''}`} onClick={() => setCurrentPage('market')}>
          📊 行情
        </div>
        <div className={`nav-item ${currentPage === 'strategy' ? 'active' : ''}`} onClick={() => setCurrentPage('strategy')}>
          📈 策略
        </div>
        <div className={`nav-item ${currentPage === 'backtest' ? 'active' : ''}`} onClick={() => setCurrentPage('backtest')}>
          🎯 回测
        </div>
        <div className={`nav-item ${currentPage === 'options' ? 'active' : ''}`} onClick={() => setCurrentPage('options')}>
          🧩 期权
        </div>
        <div className={`nav-item ${currentPage === 'orders' ? 'active' : ''}`} onClick={() => setCurrentPage('orders')}>
          🧾 订单
        </div>
        <div className={`nav-item ${currentPage === 'positions' ? 'active' : ''}`} onClick={() => setCurrentPage('positions')}>
          💼 持仓
        </div>
        <div className={`nav-item ${currentPage === 'history' ? 'active' : ''}`} onClick={() => setCurrentPage('history')}>
          🗄️ 历史数据
        </div>
        <div className={`nav-item ${currentPage === 'settings' ? 'active' : ''}`} onClick={() => setCurrentPage('settings')}>
          ⚙️ 设置
        </div>
      </div>

      <PageErrorBoundary resetKey={currentPage} pageName={currentPage}>
        {pages[currentPage]}
      </PageErrorBoundary>
    </div>
  )
}

export default App
