const messages: Record<string, string> = {
  AUTH_SERVICE_UNAVAILABLE: '认证服务暂不可用，请检查本地服务与认证配置。',
  EMAIL_SERVICE_UNAVAILABLE: '邮件验证服务暂不可用，请稍后再试。',
  SESSION_EXPIRED: '会话已失效，请重新登录。',
  SESSION_CHANGED: '登录状态已变化，请重新检查会话。',
  INVALID_CREDENTIALS: '邮箱或密码错误。',
  CSRF_FAILED: '安全状态已变化，请重新操作；未自动重试。',
  ORIGIN_NOT_ALLOWED: '请求来源未被允许，请检查当前访问地址与后端配置。',
  VERIFICATION_INVALID: '验证码无效、已过期或尝试次数过多，请检查或重新申请。',
  REGISTRATION_TOKEN_INVALID: '注册凭证已失效，请重新验证邮箱；如已提交注册，也可尝试登录。',
  REGISTRATION_NOT_COMPLETED: '无法完成注册，请登录或重新验证邮箱。',
  VERSION_CONFLICT: '资料已更新，输入已保留。请先读取当前版本，再决定是否保存。',
  FORBIDDEN: '当前没有执行此操作的权限，请刷新后检查。',
  LAST_ADMIN_REQUIRED: '剧组至少需要一名有效管理员，请先完成管理员交接。',
  MEMBER_INACTIVE: '成员已不在组，请刷新成员列表。',
  USE_LEAVE_ACTION: '请使用本人退出入口离开剧组。',
  MEMBER_HAS_RESPONSIBILITIES: '成员仍有未交接的任务或安排，请先完成责任交接。',
  MEMBER_HAS_RESOURCE_RESPONSIBILITIES: '成员仍负责资源、有未结束预约或未归还实物，请先完成责任交接。',
  RESOURCE_NOT_RETURNED: '该预约仍有实物未归还，须先完成归还才能取消。',
  STOCK_QUANTITY_CONFLICT: '库存不足或数量超出可存储范围，请核对当前余额。',
  SCRIPT_NOT_CONFIRMED: '请先核对并确认正文，再设为工作版本。',
  SCRIPT_VERSION_IMMUTABLE: '已确认版本的正文不可覆盖，请录入新版本。',
  DOCUMENT_TYPE_UNSUPPORTED: '仅支持DOCX、文字型PDF和UTF-8 TXT。',
  DOCUMENT_TOO_LARGE: '单个剧本文件不能超过20MiB。',
  DOCUMENT_TEXT_TOO_LARGE: '提取的正文超过100000字符，请拆分文件。',
  DOCUMENT_PAGE_LIMIT: 'PDF超过100页，请拆分后再导入。',
  DOCUMENT_EXPANSION_LIMIT: 'DOCX解压后过大，请简化文档再导入。',
  DOCUMENT_FORMAT_MISMATCH: '文件内容与扩展名不一致，请导出有效文件。',
  DOCUMENT_EMPTY: '文件为空，请重新选择。',
  DOCUMENT_ENCRYPTED: '暂不支持加密PDF，请提供未加密的文字型PDF。',
  DOCUMENT_NO_TEXT: '没有提取到正文；扫描件需先转成文字，或直接粘贴正文。',
  DOCUMENT_ENCODING: 'TXT须采用UTF-8编码，请转换后重试。',
  DOCUMENT_PARSE_TIMEOUT: '文件解析超过时限，请简化文档或粘贴正文。',
  DOCUMENT_INVALID: '文件无法解析，请检查文档是否完整，或直接粘贴正文。',
  SOURCE_FILE_UNAVAILABLE: '原始文件暂不可读取，请联系项目维护者。',
  PDF_TEXT_UNSUPPORTED: '通告包含PDF字体暂不支持的字符，请使用网页版查看或调整相关文字。',
  PDF_EXPORT_FAILED: 'PDF生成失败，请保留网页版并稍后重试。',
  PHOTO_EMPTY: '照片文件为空，请重新选择。',
  PHOTO_TOO_LARGE: '单张勘景照片不能超过10MiB。',
  PHOTO_TYPE_UNSUPPORTED: '仅支持JPEG、PNG和静态WebP，其他格式请先转换。',
  PHOTO_ANIMATED: '暂不支持动图，请转换成静态照片后上传。',
  PHOTO_PIXEL_LIMIT: '图片像素超过2500万或尺寸无效，请缩小后上传。',
  PHOTO_RESULT_TOO_LARGE: '处理后的图片超过10MiB，请缩小尺寸或转换为JPEG。',
  PHOTO_INVALID: '图片无法解码，请检查文件是否完整。',
  PHOTO_PROCESS_TIMEOUT: '图片处理超时，请缩小图片后上传。',
  PHOTO_FILE_UNAVAILABLE: '照片暂不可读取，请刷新场地资料后重试。',
  LOCATION_PHOTO_LIMIT: '每个场地最多保留20张照片，请先移除不再需要的照片。',
  SOURCE_NOT_CONFIRMED: '请先确认来源剧本或场戏拆解。',
  RESOURCE_NEED_MISMATCH: '关联资源的类型或单位与需求不一致，请重新选择资源。',
  RESERVATION_RESOURCE_MISMATCH: '预约不属于所选资源，请重新选择预约。',
  PREFLIGHT_STALE: '检查记录已过期或依据已变化，请关闭确认窗口，重新检查并核对未知档期。',
  CURRENT_VERSION_CHANGED: '当前发布版本已变化，请重新读取并核对后再发布。',
  CALL_SHEET_NO_SESSIONS: '该日期没有正式拍摄场次，请重新选择日期。',
  CALL_SHEET_EXISTS: '该日期已有通告，请进入原通告创建新版本。',
  SOURCE_PLAN_STALE: '来源排期已更新或待复核，请按当前正式排期创建通告新版本。',
  CALL_SHEET_INCOMPLETE: '请补充集合时间和集合地点后再送审。',
  GATHERING_AFTER_SHOOT: '集合时间不能晚于当天第一场拍摄。',
  REQUIRED_RECIPIENT_MISSING: '当天参与拍摄的人员为必选接收人，不能移除。',
  RECIPIENT_INACTIVE: '接收人已离组或账号不可用，请重新核对。',
  RECIPIENT_SET_INVALID: '接收人的入组身份已变化，请核对来源排期并重新选择、审核。',
  REVIEW_REQUIRED: '请先完成当前内容和接收人集合的审核，再发布通告。',
  UNKNOWN_CONFIRMATION_REQUIRED: '请逐项确认本次检查列出的全部未知档期。',
  PLAN_BLOCKED: '仍有已知冲突或未落实事项，请先处理后重新检查。',
  PLAN_IMMUTABLE: '已发布排期不能直接编辑，请复制为新草稿。',
  PLAN_MEMBER_IN_USE: '仍有未结束的正式拍摄安排，请联系制片调整并发布后再离组。',
  RESERVATION_IN_PLAN: '预约仍用于正式拍摄安排，请先发布替代安排再取消。',
  RESOURCE_IN_PLAN: '资源仍用于正式排期，不能直接更改单位。',
  RESOURCE_DISABLED: '关联资源已停用，请解除关联或选择其他资源。',
  CREATIVE_IMMUTABLE: '仅草稿可修改；已确认内容请创建新版本。',
  EMPTY_CREATIVE: '请先填写场戏或镜头后再送审。',
  INVALID_SOURCE_RANGE: '原文段落范围不存在，请核对来源版本中的段落编号。',
  PHYSICAL_CAPACITY_CONFLICT: '超过实际可借数量、预约累计借出额度，或登记总量低于未归还数量。',
  CHECKOUT_NOT_ALLOWED: '仅能在已批准且仍有效的预约时段内借出可用资源。',
  INVALID_RETURN_TIME: '预计归还时间须在实际借出与预约结束之间。',
  RETURN_QUANTITY_CONFLICT: '归还数量超过未归还数量，或系统时间异常，请重新核对。',
  RESOURCE_TYPE_UNSUPPORTED: '该资源类型不支持此操作。',
  MEMBER_NOT_ELIGIBLE: '指派成员缺少对应业务权限，请检查岗位或授权。',
  RESOURCE_CAPACITY_CONFLICT: '该时段资源余量不足，或登记数量低于已有预约的同时占用峰值。',
  RESOURCE_UNAVAILABLE: '资源已停用，请重新读取并选择可预约资源。',
  RESOURCE_IN_USE: '仍有未结束的已批准预约，不能停用该资源。',
  INVALID_RESERVATION: '预约开始时间不能早于当前时间，数量不能超过登记总量。',
  RESERVATION_EXPIRED: '预约时段已结束，不能再批准。',
  CAPACITY_WINDOW_TOO_LARGE: '预约数据超过单次检查上限，请缩小范围或联系管理员。',
  INVALID_STATE: '当前状态不允许此操作，请重新读取。',
  IDEMPOTENCY_KEY_REUSED: '该操作标识已用于其他内容，请先核对原操作结果。',
  NOTIFICATION_OUTDATED: '安排已有更新或已失效，请查看最新通知；旧通知不能再确认知悉。',
  CONFIRMATION_NOT_REQUIRED: '这条通知不需要确认知悉。',
  MEMBERSHIP_CHANGED: '入组状态已变化，请重新读取当前资料后核对。',
  AVAILABILITY_WINDOW_TOO_DENSE: '范围内登记过多，请缩小日期范围或选择单个成员。',
  AVAILABILITY_CAPACITY: '档期登记条目达到上限，请先合并或清理已有登记。',
  MEMBER_LIST_CHANGED: '成员列表已变化，请关闭后重新选择成员。',
  INVITATION_INVALID: '邀请无效、已过期、已撤销或与当前账号不匹配。请核对收件邮箱或联系管理员。',
  INVITATION_PENDING: '该邮箱已有待接受邀请，请先核对列表；需要重新邀请时先撤销旧邀请。',
  INVITATION_NOT_PENDING: '邀请已结束，不能撤销，请刷新列表。',
  MEMBER_ALREADY_ACTIVE: '该邮箱对应的账号已经在剧组中。',
  VERSION_EXHAUSTED: '成员版本达到上限，暂不能修改，请联系维护者。',
  RATE_LIMITED: '请求过于频繁，请等待后再操作。',
  VALIDATION_ERROR: '填写内容未通过服务端校验，请检查后重试。',
  PAYLOAD_TOO_LARGE: '提交内容超过大小限制。',
  INVALID_RESPONSE: '服务响应格式不符合约定，未确认操作成功。',
  NETWORK_UNCONFIRMED: '连接中断或请求超时，操作结果未确认；未自动重试。',
  SERVICE_UNAVAILABLE: 'API暂不可用，请检查本地服务是否已启动。',
  PROPOSAL_EXPIRED: '提案已过期，请重新拟定。',
  PROPOSAL_STALE: '相关成员或权限已变化，请重新拟定并确认。',
  PROPOSAL_DEADLINE_PASSED: '任务截止时间已过，请重新拟定。',
  DIANE_DISABLED: 'Diane 尚未启用。',
  DIANE_BACKGROUND_DISABLED: 'Diane 后台执行尚未启用。',
  CONVERSATION_BUSY: '这段对话仍在处理上一条消息。',
  DIANE_BUSY: 'Diane 正在处理其他查询，请稍后再试。',
  REQUEST_FAILED: '请求未完成，请稍后检查后再试。',
  NOT_FOUND: '剧组不存在或无权访问，请刷新剧组列表。',
  PROFILE_INCOMPLETE: '请先补全昵称再进入剧组业务。',
  REQUEST_IN_PROGRESS: '请求正在处理，请稍候。',
  CHANGE_CHECK_TOO_LARGE: '当前类别超过1000个待处理目标，未更新问题记录；请继续使用实时清单处理。',
}

export class AccountError extends Error {
  constructor(readonly code: string, readonly status = 0, readonly requestId?: string, readonly retryAfter?: number) {
    super(messages[code] ?? messages.REQUEST_FAILED)
    this.name = 'AccountError'
  }
}

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

export function createHttpClient() {
  let csrf: string | null = null
  let generation = 0
  let pending: Promise<string> | null = null
  const invalidateCsrf = () => { csrf = null; generation += 1; pending = null }

  async function request(path: string, options: { method?: string; body?: unknown; form?: FormData; responseType?: 'pdf'; timeoutMs?: number; csrf?: string; signal?: AbortSignal; accepted?: number[]; idempotencyKey?: string } = {}): Promise<unknown> {
    let response: Response
    try {
      response = await fetch(`/api/v1${path}`, {
        method: options.method ?? 'GET', credentials: 'same-origin', cache: 'no-store',
        signal: options.signal ? AbortSignal.any([options.signal, AbortSignal.timeout(options.timeoutMs ?? 15_000)]) : AbortSignal.timeout(options.timeoutMs ?? 15_000),
        headers: options.body === undefined && options.form === undefined ? undefined : { ...(options.form ? {} : { 'Content-Type': 'application/json' }), ...(options.csrf ? { 'X-CSRF-Token': options.csrf } : {}), ...(options.idempotencyKey ? { 'Idempotency-Key': options.idempotencyKey } : {}) },
        body: options.form ?? (options.body === undefined ? undefined : JSON.stringify(options.body)),
      })
    } catch {
      throw new AccountError('NETWORK_UNCONFIRMED')
    }
    if (response.ok && !(options.accepted ?? [200]).includes(response.status)) throw new AccountError('INVALID_RESPONSE', response.status)
    if (response.status === 204) return undefined
    if (response.ok && options.responseType === 'pdf') {
      if (response.headers.get('Content-Type')?.split(';')[0].trim().toLowerCase() !== 'application/pdf') throw new AccountError('INVALID_RESPONSE')
      try { return await response.blob() } catch { throw new AccountError('NETWORK_UNCONFIRMED') }
    }
    let payload: unknown
    try { payload = await response.json() } catch { throw new AccountError(response.ok ? 'INVALID_RESPONSE' : 'SERVICE_UNAVAILABLE', response.status) }
    if (!response.ok) {
      const rawCode = record(payload) && record(payload.error) ? payload.error.code : undefined
      const code = typeof rawCode === 'string' && Object.hasOwn(messages, rawCode) ? rawCode : response.status >= 500 ? 'SERVICE_UNAVAILABLE' : 'REQUEST_FAILED'
      const requestId = record(payload) && typeof payload.request_id === 'string' && /^[a-f0-9]{32}$/.test(payload.request_id) ? payload.request_id : undefined
      const retry = Number(response.headers.get('Retry-After'))
      if (code === 'CSRF_FAILED' || code === 'SESSION_EXPIRED') invalidateCsrf()
      throw new AccountError(code, response.status, requestId, Number.isInteger(retry) && retry > 0 && retry <= 86_400 ? retry : undefined)
    }
    if (!record(payload) || !Object.hasOwn(payload, 'data')) throw new AccountError('INVALID_RESPONSE')
    return payload.data
  }

  async function prepareCsrf(): Promise<string> {
    if (csrf !== null) return csrf
    if (pending !== null) return pending
    const version = generation
    const next = request('/auth/csrf').then(data => {
      if (!record(data) || typeof data.csrf_token !== 'string' || !/^[a-f0-9]{64}$/.test(data.csrf_token)) throw new AccountError('INVALID_RESPONSE')
      if (version !== generation) throw new AccountError('SESSION_CHANGED')
      csrf = data.csrf_token
      return csrf
    }).finally(() => { if (pending === next) pending = null })
    pending = next
    return next
  }

  async function mutate(path: string, body: unknown, method = 'POST', accepted = [200], idempotencyKey?: string) {
    const token = await prepareCsrf()
    // Never transparently retry a mutation after CSRF failure, timeout or 5xx.
    return request(path, { method, body, csrf: token, accepted, idempotencyKey })
  }

  async function upload(path: string, form: FormData, idempotencyKey: string) {
    const token = await prepareCsrf()
    return request(path, { method: 'POST', form, csrf: token, accepted: [201], idempotencyKey, timeoutMs: 60_000 })
  }

  const downloadPdf = (path: string) => request(path, { responseType: 'pdf', timeoutMs: 60_000 }) as Promise<Blob>

  return { request, mutate, upload, downloadPdf, prepareCsrf, invalidateCsrf }
}

export type HttpClient = ReturnType<typeof createHttpClient>
