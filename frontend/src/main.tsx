import React from 'react'
import ReactDOM from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import App from './App'
import './styles.css'

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false, staleTime: 15_000, refetchOnWindowFocus: false } },
})

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ConfigProvider locale={zhCN} theme={{ token: {
      colorPrimary: '#ed3426', colorText: '#141414', colorTextLightSolid: '#141414', colorBgContainer: '#f7f5ef',
      borderRadius: 0, fontFamily: 'Arial, "Microsoft YaHei", sans-serif', boxShadow: 'none',
    } }}>
      <QueryClientProvider client={queryClient}><App /></QueryClientProvider>
    </ConfigProvider>
  </React.StrictMode>,
)
