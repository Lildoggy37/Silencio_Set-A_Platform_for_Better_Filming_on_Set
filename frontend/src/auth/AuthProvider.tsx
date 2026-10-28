import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { AccountError, createAccountClient } from '../api/accounts'
import type { AccountClient, User } from '../api/accounts'
import { createHttpClient } from '../api/http'
import { createCrewClient } from '../api/crews'
import type { CrewClient } from '../api/crews'
import { createTaskClient } from '../api/tasks'
import type { TaskClient } from '../api/tasks'
import { createNotificationClient } from '../api/notifications'
import type { NotificationClient } from '../api/notifications'
import { createAvailabilityClient } from '../api/availability'
import type { AvailabilityClient } from '../api/availability'
import { createResourceClient } from '../api/resources'
import type { ResourceClient } from '../api/resources'

type AuthState = { status: 'checking' } | { status: 'anonymous' } | { status: 'unavailable'; error: unknown } | { status: 'authenticated'; user: User }
type AuthContextValue = {
  state: AuthState; api: AccountClient; crews: CrewClient; tasks: TaskClient; notifications: NotificationClient; availability: AvailabilityClient; resources: ResourceClient; busy: boolean
  refresh: () => Promise<void>
  login: (email: string, password: string) => Promise<void>
  logout: () => Promise<void>
  saveProfile: (name: string, version: number) => Promise<void>
  reloadProfile: () => Promise<User>
}
const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [http] = useState(createHttpClient)
  const [api] = useState(() => createAccountClient(http))
  const [crews] = useState(() => createCrewClient(http))
  const [tasks] = useState(() => createTaskClient(http))
  const [notifications] = useState(() => createNotificationClient(http))
  const [availability] = useState(() => createAvailabilityClient(http))
  const [resources] = useState(() => createResourceClient(http))
  const [state, setState] = useState<AuthState>({ status: 'checking' })
  const [busy, setBusy] = useState(false)
  const current = useRef(state)
  current.current = state
  const epoch = useRef(0)
  const locked = useRef<number | null>(null)
  const channel = useRef<BroadcastChannel | null>(null)
  const queries = useQueryClient()

  const broadcast = () => channel.current?.postMessage('session-changed')
  const hidePrivateData = useCallback(() => { queries.clear() }, [queries])

  const checkSession = useCallback(async (quiet = false) => {
    if (quiet && locked.current !== null) return
    const ticket = ++epoch.current
    if (!quiet) { hidePrivateData(); setState({ status: 'checking' }) }
    try {
      const user = await api.me()
      if (user !== null) await api.prepareCsrf()
      if (ticket !== epoch.current) return
      if (user === null || current.current.status !== 'authenticated' || current.current.user.id !== user.id) hidePrivateData()
      setState(user === null ? { status: 'anonymous' } : { status: 'authenticated', user })
    } catch (error) {
      if (ticket === epoch.current) { hidePrivateData(); setState({ status: 'unavailable', error }) }
    }
  }, [api, hidePrivateData])

  useEffect(() => {
    void checkSession()
    const onFocus = () => { void checkSession(true) }
    window.addEventListener('focus', onFocus)
    if (typeof BroadcastChannel !== 'undefined') {
      const instance = new BroadcastChannel('silencio-session')
      channel.current = instance
      instance.onmessage = event => {
        if (event.data !== 'session-changed') return
        api.invalidateCsrf()
        void checkSession()
      }
    }
    return () => { epoch.current += 1; window.removeEventListener('focus', onFocus); channel.current?.close(); channel.current = null }
  }, [api, checkSession])

  async function withLock(action: (ticket: number) => Promise<void>) {
    if (locked.current !== null) throw new AccountError('REQUEST_IN_PROGRESS')
    const ticket = ++epoch.current
    locked.current = ticket
    setBusy(true)
    try { await action(ticket) }
    finally { if (locked.current === ticket) { locked.current = null; setBusy(false) } }
  }

  const value: AuthContextValue = {
    state, api, crews, tasks, notifications, availability, resources, busy,
    refresh: () => checkSession(),
    login: (email, password) => withLock(async ticket => {
      try {
        await api.login(email, password)
        hidePrivateData()
        await api.prepareCsrf()
        const user = await api.me()
        if (ticket !== epoch.current) throw new AccountError('SESSION_CHANGED')
        if (user === null) throw new AccountError('SESSION_EXPIRED', 401)
        setState({ status: 'authenticated', user })
        broadcast()
      } catch (error) {
        if (ticket === epoch.current && !(error instanceof AccountError && error.code === 'INVALID_CREDENTIALS')) {
          hidePrivateData(); setState({ status: 'unavailable', error }); broadcast()
        }
        throw error
      }
    }),
    logout: () => withLock(async ticket => {
      try {
        await api.logout()
        if (ticket === epoch.current) { hidePrivateData(); setState({ status: 'anonymous' }) }
      } catch (error) {
        if (ticket === epoch.current) { hidePrivateData(); setState({ status: 'unavailable', error }) }
        throw error
      } finally { api.invalidateCsrf(); broadcast() }
    }),
    saveProfile: (name, version) => withLock(async ticket => {
      const snapshot = current.current
      if (snapshot.status !== 'authenticated') throw new AccountError('SESSION_EXPIRED', 401)
      try {
        const profile = await api.saveProfile(name, version)
        if (ticket !== epoch.current) throw new AccountError('SESSION_CHANGED')
        setState({ status: 'authenticated', user: { ...snapshot.user, ...profile } })
      } catch (error) {
        if (ticket === epoch.current && error instanceof AccountError && error.code === 'SESSION_EXPIRED') {
          hidePrivateData(); setState({ status: 'anonymous' })
        }
        throw error
      }
    }),
    reloadProfile: async () => {
      const snapshot = current.current
      const ticket = epoch.current
      const user = await api.me()
      if (ticket !== epoch.current) throw new AccountError('SESSION_CHANGED')
      if (user === null) { hidePrivateData(); setState({ status: 'anonymous' }); throw new AccountError('SESSION_EXPIRED', 401) }
      if (snapshot.status !== 'authenticated' || user.id !== snapshot.user.id) {
        hidePrivateData(); setState({ status: 'unavailable', error: new AccountError('SESSION_CHANGED') })
        throw new AccountError('SESSION_CHANGED')
      }
      setState({ status: 'authenticated', user })
      return user
    },
  }
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth() {
  const value = useContext(AuthContext)
  if (value === null) throw new Error('AuthProvider is required')
  return value
}
