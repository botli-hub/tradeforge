import { useState, useEffect } from 'react'
import { getStrategies, createStrategy, deleteStrategy } from '../services/api'

interface Strategy {
  id: string
  name: string
  status: string
  mode: string
}

export default function StrategyPage() {
  const [strategies, setStrategies] = useState<Strategy[]>([])
  const [showEditor, setShowEditor] = useState(false)
  const [newName, setNewName] = useState('')

  useEffect(() => {
    loadStrategies()
  }, [])

  async function loadStrategies() {
    try {
      const data = await getStrategies()
      setStrategies(data)
    } catch (e) {
      console.error('Failed to load strategies:', e)
    }
  }

  async function handleCreate() {
    if (!newName) return
    
    const defaultConfig = {
      version: "1.0",
      strategy_id: "",
      name: newName,
      symbols: ["AAPL"],
      timeframe: "1d",
      indicators: [
        { name: "ma_fast", type: "MA", period: 20 },
        { name: "ma_slow", type: "MA", period: 50 }
      ],
      conditions: {
        entry: { type: "AND", rules: [{ id: "c1", indicator: "ma_fast", op: "cross_above", ref: "ma_slow" }] },
        exit: { type: "OR", rules: [{ id: "e1", indicator: "ma_fast", op: "cross_below", ref: "ma_slow" }] }
      },
      position_sizing: { type: "fixed_amount", value: 10000 },
      risk_rules: { initial_capital: 100000, fee_rate: 0.0003, slippage: 0.001, max_position_pct: 0.5 }
    }

    try {
      await createStrategy({ name: newName, config: defaultConfig })
      setNewName('')
      setShowEditor(false)
      loadStrategies()
    } catch (e) {
      console.error('Failed to create strategy:', e)
    }
  }

  async function handleDelete(id: string) {
    if (!confirm('确定删除?')) return
    try {
      await deleteStrategy(id)
      loadStrategies()
    } catch (e) {
      console.error('Failed to delete:', e)
    }
  }

  return (
    <div className="page">
      <div style={{display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20}}>
        <h2>我的策略</h2>
        <button className="btn" onClick={() => setShowEditor(true)}>+ 新建策略</button>
      </div>

      {/* 新建策略编辑器 */}
      {showEditor && (
        <div className="card" style={{border: '2px solid #4cc9f0'}}>
          <h3 style={{color: '#4cc9f0', marginBottom: 16}}>新建策略</h3>
          
          <div className="editor-section">
            <h4>策略名称</h4>
            <input 
              type="text" 
              placeholder="输入策略名称"
              value={newName}
              onChange={e => setNewName(e.target.value)}
              style={{width: '100%'}}
            />
          </div>

          <div style={{display: 'flex', gap: 12, justifyContent: 'flex-end'}}>
            <button className="btn-outline" onClick={() => setShowEditor(false)}>取消</button>
            <button className="btn" onClick={handleCreate}>创建</button>
          </div>
        </div>
      )}

      {/* 策略列表 */}
      {strategies.length === 0 ? (
        <div className="empty-state">
          <h3>暂无策略</h3>
          <p>创建第一个策略开始量化之旅</p>
        </div>
      ) : (
        strategies.map(s => (
          <div key={s.id} className="card">
            <div className="strategy-card">
              <div className="strategy-info">
                <h3>📈 {s.name}</h3>
                <p>模式: {s.mode} | <span className={`tag ${s.status === 'ready' ? 'ready' : 'draft'}`}>{s.status}</span></p>
              </div>
              <div className="strategy-actions">
                <button className="btn-outline">编辑</button>
                <button className="btn-outline">回测</button>
                <button className="btn-outline" style={{color: '#e94560', borderColor: '#e94560'}} onClick={() => handleDelete(s.id)}>删除</button>
              </div>
            </div>
          </div>
        ))
      )}
    </div>
  )
}
