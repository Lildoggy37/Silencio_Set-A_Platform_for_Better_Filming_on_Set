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
  MEMBER_HAS_RESOURCE_RESPONSIBILITIES: '成员仍负责资源或有未结束预约，请先转交资源、取消或处理预约。',
  MEMBER_NOT_ELIGIBLE: '指派成员缺少对应业务权限，请检查岗位或授权。',
  RESOURCE_CAPACITY_CONFLICT: '该时段资源余量不足，或登记数量低于已有预约的同时占用峰值。',
  RESOURCE_UNAVAILABLE: '资源已停用，请重新读取并选择可预约资源。',
  RESOURCE_IN_USE: '仍有未结束的已批准预约，不能停用该资源。',
  INVALID_RESERVATION: '预约开始时间不能早于当前时间，数量不能超过登记总量。',
  RESERVATION_EXPIRED: '预约时段已结束，不能再批准。',
  CAPACITY_WINDOW_TOO_LARGE: '预约数据超过单次检查上限，请缩小范围或联系管理员。',
  INVALID_STATE: '当前状态不允许此操作，请重新读取。',
  IDEMPOTENCY_KEY_REUSED: '该操作标识已用于其他内容，请先核对原操作结果。',
  NOTIFICATION_OUTDATED: '任务已有更新，请查看最新通知和当前任务；旧通知不能再确认知悉。',
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
  REQUEST_FAILED: '请求未完成，请稍后检查后再试。',
  NOT_FOUND: '剧组不存在或无权访问，请刷新剧组列表。',
  PROFILE_INCOMPLETE: '请先补全昵称再进入剧组业务。',
  REQUEST_IN_PROGRESS: '请求正在处理，请稍候。',
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

  async function request(path: string, options: { method?: string; body?: unknown; csrf?: string; signal?: AbortSignal; accepted?: number[]; idempotencyKey?: string } = {}): Promise<unknown> {
    let response: Response
    try {
      response = await fetch(`/api/v1${path}`, {
        method: options.method ?? 'GET', credentials: 'same-origin', cache: 'no-store',
        signal: options.signal ? AbortSignal.any([options.signal, AbortSignal.timeout(15_000)]) : AbortSignal.timeout(15_000),
        headers: options.body === undefined ? undefined : { 'Content-Type': 'application/json', ...(options.csrf ? { 'X-CSRF-Token': options.csrf } : {}), ...(options.idempotencyKey ? { 'Idempotency-Key': options.idempotencyKey } : {}) },
        body: options.body === undefined ? undefined : JSON.stringify(options.body),
      })
    } catch {
      throw new AccountError('NETWORK_UNCONFIRMED')
    }
    if (response.ok && !(options.accepted ?? [200]).includes(response.status)) throw new AccountError('INVALID_RESPONSE', response.status)
    if (response.status === 204) return undefined
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

  return { request, mutate, prepareCsrf, invalidateCsrf }
}

export type HttpClient = ReturnType<typeof createHttpClient>
