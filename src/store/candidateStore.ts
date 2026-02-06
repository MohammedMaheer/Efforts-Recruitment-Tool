import { create } from 'zustand'

export interface Candidate {
  id: string
  name: string
  email: string
  phone: string
  location: string
  experience: number
  matchScore: number
  status: 'Strong' | 'Partial' | 'Reject'
  skills: string[]
  resumeUrl: string
  appliedDate: string
  avatar?: string
  summary: string
  isShortlisted?: boolean
  hasResume?: boolean
  jobCategory: string
  linkedin?: string
  education: {
    degree: string
    field?: string
    institution: string
    year: string
  }[]
  workHistory: {
    title: string
    company: string
    duration: string
    description: string
  }[]
  evaluation?: {
    strengths: string[]
    gaps: string[]
    recommendation: string
  }
}

interface CandidateState {
  candidates: Candidate[]
  shortlistedIds: string[]
  addCandidate: (candidate: Candidate) => void
  updateCandidate: (id: string, updates: Partial<Candidate>) => void
  toggleShortlist: (id: string) => void
  isShortlisted: (id: string) => boolean
  setCandidates: (candidates: Candidate[]) => void
}

export const useCandidateStore = create<CandidateState>((set, get) => ({
  candidates: [],
  shortlistedIds: [],
  addCandidate: (candidate) =>
    set((state) => ({ candidates: [...state.candidates, candidate] })),
  updateCandidate: (id, updates) =>
    set((state) => ({
      candidates: state.candidates.map((c) =>
        c.id === id ? { ...c, ...updates } : c
      ),
    })),
  toggleShortlist: (id) =>
    set((state) => ({
      shortlistedIds: state.shortlistedIds.includes(id)
        ? state.shortlistedIds.filter((sid) => sid !== id)
        : [...state.shortlistedIds, id],
    })),
  isShortlisted: (id) => get().shortlistedIds.includes(id),
  setCandidates: (candidates: Candidate[]) => set({ candidates }),
}))
