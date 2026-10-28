import { lazy, Suspense, useEffect, useLayoutEffect, useState } from 'react'
import { AuthProvider, useAuth } from './auth/AuthProvider'
import { ScreenBoundary } from './components/ScreenBoundary'

const Workspace = lazy(() => import('./Workspace'))
const AuthScreen = lazy(() => import('./auth/AccountScreens').then(module => ({ default: module.AuthScreen })))
const ProfileScreen = lazy(() => import('./auth/AccountScreens').then(module => ({ default: module.ProfileScreen })))
const InvitationAcceptance = lazy(() => import('./crews/InvitationScreens').then(module => ({ default: module.InvitationAcceptance })))

function Application({ invitation, clearInvitation }: { invitation: string | null; clearInvitation: () => void }) {
  const { state } = useAuth()
  const [preview, setPreview] = useState(false)
  const [editing, setEditing] = useState(false)
  const [joined, setJoined] = useState<{ userId: string; crewId: string } | null>(null)
  const userId = state.status === 'authenticated' ? state.user.id : null
  useEffect(() => { setEditing(false); if (userId !== null) setPreview(false) }, [userId])
  if (state.status === 'authenticated') {
    if (!state.user.profile_completed || editing) return <ProfileScreen key={state.user.id} user={state.user} onDone={() => setEditing(false)} />
    if (invitation) return <InvitationAcceptance key={state.user.id} token={invitation} email={state.user.email}
      onLater={clearInvitation} onJoined={crewId => { setJoined({ userId: state.user.id, crewId }); clearInvitation() }} />
    return <Workspace key={state.user.id} user={state.user} preview={false} initialCrewId={joined?.userId === state.user.id ? joined.crewId : undefined} onAccount={() => setEditing(true)} />
  }
  if (preview) return <Workspace key="preview" preview onAccount={() => setPreview(false)} />
  return <AuthScreen onPreview={() => setPreview(true)} />
}

export default function App() {
  const [invitation, setInvitation] = useState(() => {
    const value = new URLSearchParams(window.location.hash.slice(1)).get('invitation')
    return value && /^[A-Za-z0-9_-]{43}$/.test(value) ? value : null
  })
  useLayoutEffect(() => {
    const capture = () => {
      const params = new URLSearchParams(window.location.hash.slice(1))
      if (!params.has('invitation')) return
      const value = params.get('invitation')
      if (value && /^[A-Za-z0-9_-]{43}$/.test(value)) setInvitation(value)
      window.history.replaceState(window.history.state, '', window.location.pathname + window.location.search)
    }
    capture(); window.addEventListener('hashchange', capture)
    return () => window.removeEventListener('hashchange', capture)
  }, [])
  return <ScreenBoundary><AuthProvider><Suspense fallback={<main className="workspace" role="status">正在加载页面…</main>}><Application invitation={invitation} clearInvitation={() => setInvitation(null)} /></Suspense></AuthProvider></ScreenBoundary>
}
