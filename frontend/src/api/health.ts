export type DependencyStatus = 'ok' | 'unavailable' | 'not_configured'
export type Dependencies = Record<'database' | 'redis' | 'rabbitmq', DependencyStatus>
export type Health = { ready: boolean; dependencies?: Dependencies; requestId: string }

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function parseDependencies(value: unknown): Dependencies {
  const names = ['database', 'redis', 'rabbitmq'] as const
  if (!isRecord(value) || names.some(name => !['ok', 'unavailable', 'not_configured'].includes(String(value[name])))) {
    throw new Error('健康检查响应格式不符合约定')
  }
  return Object.fromEntries(names.map(name => [name, value[name]])) as Dependencies
}

export async function fetchHealth(kind: 'live' | 'ready', signal?: AbortSignal): Promise<Health> {
  const response = await fetch(`/api/v1/health/${kind}`, {
    signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(4000)]) : AbortSignal.timeout(4000),
    cache: 'no-store',
  })
  const body: unknown = await response.json()
  if (!isRecord(body) || typeof body.request_id !== 'string') {
    throw new Error('健康检查响应格式不符合约定')
  }
  if (kind === 'ready' && response.status === 503 && isRecord(body.error)
      && body.error.code === 'DEPENDENCIES_NOT_READY' && isRecord(body.error.details)) {
    return { ready: false, dependencies: parseDependencies(body.error.details.dependencies), requestId: body.request_id }
  }
  if (!response.ok) throw new Error('服务暂不可用')
  if (!isRecord(body.data) || body.data.status !== (kind === 'live' ? 'alive' : 'ready')) {
    throw new Error('健康检查响应格式不符合约定')
  }
  return {
    ready: true,
    dependencies: kind === 'ready' ? parseDependencies(body.data.dependencies) : undefined,
    requestId: body.request_id,
  }
}
