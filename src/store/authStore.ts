import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import config from '@/config'

interface User {
  id: string
  email: string
  name: string
  firstName?: string
  role: string
}

interface AuthState {
  user: User | null
  isAuthenticated: boolean
  login: (email: string, password: string) => Promise<void>
  logout: () => void
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      user: null,
      isAuthenticated: false,
      login: async (email: string, password: string) => {
        try {
          const response = await fetch(`${config.endpoints.auth}/login`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email, password }),
          })
          
          if (!response.ok) {
            const error = await response.json()
            throw new Error(error.detail || 'Login failed')
          }
          
          const data = await response.json()
          const user: User = data.user
          
          set({ user, isAuthenticated: true })
        } catch (error) {
          console.error('Login error:', error)
          throw error
        }
      },
      logout: () => {
        set({ user: null, isAuthenticated: false })
      },
    }),
    {
      name: 'auth-storage',
    }
  )
)
