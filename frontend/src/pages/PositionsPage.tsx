export default function PositionsPage() {
  return (
    <div className="page">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20, gap: 12, flexWrap: 'wrap' }}>
        <h2>持仓</h2>
      </div>

      <div className="card empty-state">
        <h3>持仓查询已停用</h3>
        <p>全项目已停止请求 /api/trading/status、/api/trading/positions、/api/trading/account。</p>
        <p>2032 Plan 的持仓明细请改在本地数据库维护，实时价格仍走行情接口。</p>
      </div>
    </div>
  )
}
