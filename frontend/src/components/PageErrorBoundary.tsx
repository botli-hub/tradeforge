import React from 'react'

type Props = {
  children: React.ReactNode
  resetKey?: string
  pageName?: string
}

type State = {
  hasError: boolean
  message: string
}

export default class PageErrorBoundary extends React.Component<Props, State> {
  state: State = {
    hasError: false,
    message: '',
  }

  static getDerivedStateFromError(error: Error): State {
    return {
      hasError: true,
      message: error?.message || '页面渲染异常',
    }
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error(`[PageErrorBoundary] ${this.props.pageName || 'page'} crashed`, error, info)
  }

  componentDidUpdate(prevProps: Props) {
    if (this.state.hasError && prevProps.resetKey !== this.props.resetKey) {
      this.setState({ hasError: false, message: '' })
    }
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="page active">
          <div className="card error-boundary-card">
            <h2>{this.props.pageName || '当前页面'}暂时不可用</h2>
            <p style={{ color: '#c9d7ee', marginTop: 8 }}>
              页面运行时出现异常，但应用没有崩掉。你可以切换页面继续使用，或点击下方按钮重试。
            </p>
            {this.state.message && (
              <div className="error-boundary-message">错误信息：{this.state.message}</div>
            )}
            <div style={{ display: 'flex', gap: 12, marginTop: 16 }}>
              <button className="btn" onClick={() => this.setState({ hasError: false, message: '' })}>
                重试当前页面
              </button>
              <button className="btn-outline" onClick={() => window.location.reload()}>
                刷新应用
              </button>
            </div>
          </div>
        </div>
      )
    }

    return this.props.children
  }
}
