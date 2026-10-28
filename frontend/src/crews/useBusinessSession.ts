import { useEffect, useRef } from 'react'
import { useAuth } from '../auth/AuthProvider'
import { AccountError } from '../api/accounts'

export function useBusinessSession(error: unknown) {
  const { refresh } = useAuth()
  const refreshRef = useRef(refresh)
  refreshRef.current = refresh
  useEffect(() => {
    if (error instanceof AccountError && ['SESSION_EXPIRED', 'PROFILE_INCOMPLETE'].includes(error.code)) void refreshRef.current()
  }, [error])
}
