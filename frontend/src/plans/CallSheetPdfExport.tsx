import { useState } from 'react'
import { Button } from 'antd'
import { useAuth } from '../auth/AuthProvider'
import { ErrorNotice } from '../auth/AccountScreens'
import { useBusinessSession } from '../crews/useBusinessSession'
import type { CallSheetVersion } from '../api/callSheets'

export function CallSheetPdfExport({ crewId, version }: { crewId: string; version: CallSheetVersion }) {
  const { callSheets } = useAuth()
  const [pending, setPending] = useState(false), [error, setError] = useState<unknown>(null)
  useBusinessSession(error)
  async function download() {
    setPending(true); setError(null)
    try {
      const blob = await callSheets.exportPdf(crewId, version.id, version.lock_version)
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = `SilencioSet-${version.call_sheet.shooting_date}-v${version.version_no}-${version.id.slice(0,8)}.pdf`
      document.body.append(anchor); anchor.click(); anchor.remove()
      const release = URL.revokeObjectURL.bind(URL)
      window.setTimeout(() => release(url), 1000)
    } catch (failure) { setError(failure) }
    finally { setPending(false) }
  }
  return <div className="role-editor"><div className="member-actions"><Button loading={pending} onClick={() => { void download() }}>导出当前所选版本PDF</Button></div>
    <p className="caption">导出所选通告版本并标明生成时的状态；离线文件不会自动更新，未发布及失效版本不作为当前有效拍摄安排。</p>
    <ErrorNotice error={error} />{error != null && <p className="caption">网页版仍可查看。请刷新通告，核对当前版本后再导出；不会返回旧PDF替代失败结果。</p>}
  </div>
}
