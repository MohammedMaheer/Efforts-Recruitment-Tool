import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import config from '@/config'

interface User {
  id: string
  email: string
  name: string
  firstName?: string
  role: string
  company?: string
  phone?: string
  avatarUrl?: string
}

interface AuthState {
  user: User | null
  token: string | null
  isAuthenticated: boolean
  isLoading: boolean
  login: (email: string, password: string) => Promise<void>
  register: (email: string, password: string, name?: string) => Promise<void>
  logout: () => void
  verifyToken: () => Promise<boolean>
  updateProfile: (profile: Partial<User>) => Promise<void>
  changePassword: (currentPassword: string, newPassword: string) => Promise<void>
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set, get) => ({
      user: null,
      token: null,
      isAuthenticated: false,
      isLoading: false,
      
      login: async (email: string, password: string) => {
        set({ isLoading: true })
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
          set({ 
            user: data.user, 
            token: data.token,
            isAuthenticated: true,
            isLoading: false
          })
        } catch (error) {
          set({ isLoading: false })
          console.error('Login error:', error)
          throw error
        }
      },
      
      register: async (email: string, password: string, name?: string) => {
        set({ isLoading: true })
        try {
          const response = await fetch(`${config.endpoints.auth}/register`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email, password, name }),
          })
          
          if (!response.ok) {
            const error = await response.json()
            throw new Error(error.detail || 'Registration failed')
          }
          
          const data = await response.json()
          set({ 
            user: data.user, 
            token: data.token,
            isAuthenticated: true,
            isLoading: false
          })
        } catch (error) {
          set({ isLoading: false })
          console.error('Registration error:', error)
          throw error
        }
      },
      
      logout: () => {
        set({ user: null, token: null, isAuthenticated: false })
      },
      
      verifyToken: async () => {
        const { token } = get()
        if (!token) {
          set({ isAuthenticated: false, user: null })
          return false
        }
        
        try {
          const response = await fetch(`${config.endpoints.auth}/me`, {
            headers: { 
              'Authorization': `Bearer ${token}`,
              'Content-Type': 'application/json'
            },
          })
          
          if (!response.ok) {
            set({ isAuthenticated: false, user: null, token: null })
            return false
          }
          
          const data = await response.json()
          set({ user: data.user, isAuthenticated: true })
          return true
        } catch (error) {
          console.error('Token verification failed:', error)
          set({ isAuthenticated: false, user: null, token: null })
          return false
        }
      },
      
      updateProfile: async (profile: Partial<User>) => {
        const { token } = get()
        if (!token) throw new Error('Not authenticated')
        
        const response = await fetch(`${config.apiUrl}/api/users/profile`, {
          method: 'PUT',
          headers: { 
            'Authorization': `Bearer ${token}`,
            'Content-Type': 'application/json'
          },
          body: JSON.stringify({
            firstName: profile.firstName || profile.name?.split(' ')[0] || '',
            lastName: profile.name?.split(' ').slice(1).join(' ') || '',
            email: profile.email,
            company: profile.company,
            phone: profile.phone
          }),
        })
        
        if (!response.ok) {
          const error = await response.json()
          throw new Error(error.detail || 'Failed to update profile')
        }
        
        const data = await response.json()
        set({ user: data.user })
      },
      
      changePassword: async (currentPassword: string, newPassword: string) => {
        const { token } = get()
        if (!token) throw new Error('Not authenticated')
        
        const response = await fetch(`${config.apiUrl}/api/users/password`, {
          method: 'PUT',
          headers: { 
            'Authorization': `Bearer ${token}`,
            'Content-Type': 'application/json'
          },
          body: JSON.stringify({ currentPassword, newPassword }),
        })
        
        if (!response.ok) {
          const error = await response.json()
          throw new Error(error.detail || 'Failed to change password')
        }
      },
    }),
    {
      name: 'auth-storage',
      partialize: (state) => ({ 
        user: state.user, 
        token: state.token,
        isAuthenticated: state.isAuthenticated 
      }),
