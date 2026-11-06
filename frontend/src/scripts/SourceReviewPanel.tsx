import { useRef, useState } from 'react'
import { Button, Input, Modal } from 'antd'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useAuth } from '../auth/AuthProvider'
import { ErrorNotice } from '../auth/AccountScreens'
import { AccountError } from '../api/accounts'
import type { CreativeKind } from '../api/creative'
import type { SourceAssessment, SourceReviewBody, SourceReview } from '../api/sourceReviews'
import { useBusinessSession } from '../crews/useBusinessSession'

export const sourceStatuses = { current: '来源为当前工作稿', review_required: '来源已变化，待复核', still_applicable: '已复核：仍适用', needs_revision: '已复核：需要修订', no_working_version: '尚未设置工作稿' }
type Props = { crewId: string; userId: string; kind: CreativeKind; objectId: string; permissions: string[] }

export function SourceReviewPanel(props: Props) {
  const [open, setOpen] = useState(false)
  return <><Button onClick={() => setOpen(true)}>检查资料来源</Button>{open && <SourceReviewDialog {...props} onClose={() => setOpen(false)} />}</>
}

function SourceReviewDialog({ crewId, userId, kind, objectId, permissions, onClose }: Props & { onClose: () => void }) {
  const { creative } = useAuth()
  const cache = useQueryClient()
  const [note, setNote] = useState(''), [conclusion, setConclusion] = useState<SourceReview['conclusion']>('needs_revision')
  const [command, setCommand] = useState<{ body: SourceReviewBody; key: string } | null>(null)
  const [error, setError] = useState<unknown>(null), [busy, setBusy] = useState(false), [uncertain, setUncertain] = useState(false), [mustReload, setMustReload] = useState(false)
  const [history, setHistory] = useState(false), [page, setPage] = useState(1), [compare, setCompare] = useState(false), [saved, setSaved] = useState(false)
  const lock = useRef(false)
  const query = useQuery({ queryKey: ['crews', userId, crewId, 'source-assessment', kind, objectId], queryFn: ({ signal }) => creative.sourceAssessment(crewId, kind, objectId, signal), retry: false })
  const reviews = useQuery({ queryKey: ['crews', userId, crewId, 'source-reviews', kind, objectId, page], queryFn: ({ signal }) => creative.sourceReviewHistory(crewId, kind, objectId, page, signal), enabled: history, retry: false })
  useBusinessSession(error ?? query.error ?? reviews.error)
  const data = query.isError ? undefined : query.data
  const canReview = permissions.includes(`${kind === 'breakdowns' ? 'scene' : 'storyboard'}.review`)
  async function refresh() {
    setCompare(false); setSaved(false)
    const fresh = await query.refetch()
    if (fresh.isSuccess) { setMustReload(false); setError(null) }
  }
  async function submit() {
    if (lock.current) return
    lock.current = true; setBusy(true); setError(null)
    try {
      await creative.reviewSource(crewId, kind, objectId, command!.body, command!.key)
      setCommand(null); setUncertain(false); setNote(''); setSaved(true); setCompare(false)
      void cache.invalidateQueries({ queryKey: ['crews', userId, crewId] })
    } catch (failure) {
      setError(failure)
      const unknown = !(failure instanceof AccountError) || ['NETWORK_UNCONFIRMED', 'INVALID_RESPONSE', 'SERVICE_UNAVAILABLE'].includes(failure.code) || failure.status >= 500
      setUncertain(unknown)
      if (!unknown) { setCommand(null); setMustReload(true) }
    } finally { lock.current = false; setBusy(false) }
  }
  return <Modal title="资料来源与适用性复核" open width={1100} footer={null} closable={!busy && !uncertain} keyboard={!busy && !uncertain} mask={{ closable: !busy && !uncertain }} onCancel={onClose}>
    <ErrorNotice error={error ?? query.error} />{query.isPending && <p role="status">正在检查资料来源…</p>}
    {saved && <p role="status">复核记录已保存，请按最新依据重新检查并发布排期。</p>}
    <Button aria-label="刷新来源依据" disabled={!!command} loading={query.isFetching} onClick={() => { void refresh() }}>刷新来源依据</Button>
    {data && <><p>{data.script_title} · 资料第 {data.version_no} 版</p><p>固定来源：剧本第 {data.source_script_version_no} 版；当前工作稿：{data.working_script_version_no ? `第 ${data.working_script_version_no} 版` : '未设置'}</p><p role={data.status === 'review_required' || data.status === 'needs_revision' ? 'alert' : 'status'}>{sourceStatuses[data.status]}</p>
      <p className="caption">复核只记录该份资料在当前工作稿下是否仍适用，不改写原来源或已发布安排。需要修订时请基于工作稿创建新资料。</p>
      {data.latest_review && <blockquote><p>{data.latest_review.review_note}</p><p>当前结论依据：工作稿变更序号 {data.latest_review.working_revision}</p></blockquote>}
      <div className="member-actions"><Button disabled={!!command} onClick={() => setCompare(!compare)}>对照剧本文字</Button><Button disabled={!!command} onClick={() => setHistory(!history)}>查看来源复核历史</Button></div>
      {compare && <ScriptComparison key={`${data.source_script_version_id}-${data.working_revision}`} crewId={crewId} userId={userId} data={data} />}
      {canReview && data.status !== 'current' && data.working_script_version_id && data.review_status === 'confirmed' && <form className="role-editor" onSubmit={event => { event.preventDefault(); setSaved(false); setCommand({ key: crypto.randomUUID(), body: { expected_lock_version: data.lock_version, expected_working_version_id: data.working_script_version_id!, expected_working_revision: data.working_revision, expected_review_id: data.latest_review?.id ?? null, conclusion, review_note: note.trim() } }) }}>
        <fieldset disabled={!!command || mustReload || query.isFetching}><legend>对当前工作稿的适用性</legend><label>复核结论<select value={conclusion} onChange={event => setConclusion(event.target.value as SourceReview['conclusion'])}><option value="needs_revision">需要修订</option><option value="still_applicable">核对后仍适用</option></select></label><label>复核依据<Input.TextArea required maxLength={1000} value={note} onChange={event => setNote(event.target.value)} placeholder="说明已核对的段落、变化及判断依据" /></label><Button htmlType="submit" disabled={!note.trim()}>核对来源复核结论</Button></fieldset>
      </form>}
      {data.review_status !== 'confirmed' && data.status !== 'current' && <p>请先完成资料内容审核，再由有权审核的成员记录对工作稿的适用性。</p>}
    </>}
    {mustReload && <p role="alert">提交依据已变化。请刷新来源依据，保留的说明需重新核对后再提交。</p>}
    {command && <section><h4>确认来源复核</h4><p>工作稿变更序号 {command.body.expected_working_revision} · {command.body.conclusion === 'still_applicable' ? '核对后仍适用' : '需要修订'}</p><p className="preserve-lines">{command.body.review_note}</p>{uncertain && <p role="alert">结果尚未确认，请用原复核请求重试。</p>}<Button type="primary" aria-label={uncertain ? '用原复核请求重试' : '确认来源复核'} loading={busy} onClick={() => { void submit() }}>{uncertain ? '用原复核请求重试' : '确认来源复核'}</Button>{!uncertain && !busy && <Button onClick={() => setCommand(null)}>返回修改结论</Button>}</section>}
    {history && <><ErrorNotice error={reviews.error} />{!reviews.isError && reviews.data && <><ul className="resource-bookings">{reviews.data.items.map(row => <li key={row.id}><strong>{row.conclusion === 'still_applicable' ? '仍适用' : '需要修订'} · 工作稿变更序号 {row.working_revision}{data?.latest_review?.id === row.id ? ' · 当前结论' : ' · 历史记录'}</strong><p className="preserve-lines">{row.review_note}</p><p>复核时间：{new Date(row.reviewed_at).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' })}（北京时间）</p></li>)}</ul><div className="list-pagination"><Button disabled={page === 1 || !!command} onClick={() => setPage(page-1)}>上一页来源复核</Button><Button disabled={page*20 >= reviews.data.total || !!command} onClick={() => setPage(page+1)}>下一页来源复核</Button></div></>}</>}
  </Modal>
}

function ScriptComparison({ crewId, userId, data }: { crewId: string; userId: string; data: SourceAssessment }) {
  const { scripts } = useAuth()
  const fixed = useQuery({ queryKey: ['crews', userId, crewId, 'source-review-script', data.source_script_version_id], queryFn: () => scripts.get(crewId, data.script_id, data.source_script_version_id), retry: false })
  const working = useQuery({ queryKey: ['crews', userId, crewId, 'source-review-script', data.working_script_version_id], queryFn: () => scripts.get(crewId, data.script_id, data.working_script_version_id!), enabled: !!data.working_script_version_id, retry: false })
  const error = fixed.error ?? working.error
  useBusinessSession(error)
  return <><ErrorNotice error={error} />{!error && <div className="source-review-comparison">{[{ title: '固定来源正文', value: fixed.data }, { title: '当前工作稿正文', value: working.data }].map(column => <section key={column.title}><h4>{column.title}</h4><ol className="source-review-column script-paragraphs">{column.value?.version?.paragraphs.map(paragraph => <li key={paragraph.id} value={paragraph.paragraph_no}>{paragraph.text}</li>)}</ol></section>)}</div>}</>
}
