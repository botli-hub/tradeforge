type SignalConfirmModalProps = {
  open: boolean
  symbol: string
  side: 'BUY' | 'SELL'
  price: number
  quantity: number
  orderType: 'LIMIT' | 'MARKET'
  adapterLabel: string
  envLabel: string
  onChangePrice: (value: number) => void
  onChangeQuantity: (value: number) => void
  onChangeOrderType: (value: 'LIMIT' | 'MARKET') => void
  onCancel: () => void
  onConfirm: () => void
  submitting?: boolean
}

export default function SignalConfirmModal(props: SignalConfirmModalProps) {
  const {
    open,
    symbol,
    side,
    price,
    quantity,
    orderType,
    adapterLabel,
    envLabel,
    onChangePrice,
    onChangeQuantity,
    onChangeOrderType,
    onCancel,
    onConfirm,
    submitting,
  } = props

  if (!open) return null

  return (
    <div className="modal-mask">
      <div className="modal-card">
        <div className="modal-header">
          <div>
            <div className="modal-title">信号确认</div>
            <div className="modal-subtitle">策略信号已触发，下单前请再次确认</div>
          </div>
          <span className={`tag ${side === 'BUY' ? 'positive' : 'negative'}`}>
            {side === 'BUY' ? '买入信号' : '卖出信号'}
          </span>
        </div>

        <div className="modal-grid">
          <div className="modal-field">
            <label>股票代码</label>
            <input value={symbol} readOnly />
          </div>
          <div className="modal-field">
            <label>交易通道</label>
            <input value={`${adapterLabel} / ${envLabel}`} readOnly />
          </div>
          <div className="modal-field">
            <label>订单类型</label>
            <select value={orderType} onChange={e => onChangeOrderType(e.target.value as 'LIMIT' | 'MARKET')}>
              <option value="LIMIT">限价单</option>
              <option value="MARKET">市价单</option>
            </select>
          </div>
          <div className="modal-field">
            <label>数量</label>
            <input type="number" value={quantity} onChange={e => onChangeQuantity(Number(e.target.value))} />
          </div>
          <div className="modal-field modal-field-full">
            <label>价格</label>
            <input
              type="number"
              step="0.01"
              value={price}
              onChange={e => onChangePrice(Number(e.target.value))}
              disabled={orderType === 'MARKET'}
            />
          </div>
        </div>

        <div className="modal-hint">
          信号流：<strong>策略信号 → 确认弹窗 → place_order → 订单更新 → 持仓同步</strong>
        </div>

        <div className="modal-actions">
          <button className="btn-outline" onClick={onCancel}>取消</button>
          <button className="btn" onClick={onConfirm} disabled={submitting}>
            {submitting ? '提交中...' : '确认下单'}
          </button>
        </div>
      </div>
    </div>
  )
}
