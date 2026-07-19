import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import './index.css'
import './styles/apple-ui.css'
import './styles/cyber-ui.css'
import { applyUiStyle } from './services/uiStyle'

// 尽早应用 UI 风格,避免首屏闪烁
applyUiStyle()

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
