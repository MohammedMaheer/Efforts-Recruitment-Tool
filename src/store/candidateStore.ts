import { create } from 'zustand'
import type { Candidate } from '@/types'

// Re-export Candidate from canonical source
export type { Candidate }

interface CandidateState {
  candidates: Candidate[]
  shortlistedIds: string[]
  addCandidate: (candidate: Candidate) => void
  updateCandidate: (id: string, updates: Partial<Candidate>) => void
  toggleShortlist: (id: string) => void
  isShortlisted: (id: string) => boolean
  setCandidates: (candidates: Candidate[]) => void
}

export const useCandidateStore = create<CandidateState>()((set, get) => ({
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
