import { Component } from 'react'
import type { ReactNode } from 'react'

export class ScreenBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false }
  static getDerivedStateFromError() { return { failed: true } }
  render() {
    if (!this.state.failed) return this.props.children
    return <main className="workspace" role="alert"><h1>页面加载失败</h1><p>请检查连接后刷新。刷新会清除未提交的注册进度，不会自动重复提交操作。</p><button type="button" onClick={() => window.location.reload()}>刷新页面</button></main>
  }
}
