/**
 * Candidate Store with Zustand
 * Optimized with selectors and proper TypeScript types
 */
import { create } from 'zustand';
import { devtools, subscribeWithSelector } from 'zustand/middleware';
import { immer } from 'zustand/middleware/immer';
import type { Candidate, CandidateFilters, SortOption } from '@/types';

// ============================================================================
// Types
// ============================================================================

interface CandidateState {
  // Data
  candidates: Candidate[];
  shortlistedIds: Set<string>;
  
  // UI State
  filters: CandidateFilters;
  sortBy: SortOption;
  selectedCandidateId: string | null;
  
  // Loading States
  isLoading: boolean;
  isRefreshing: boolean;
  error: string | null;
  lastFetchedAt: number | null;
}

interface CandidateActions {
  // Data Actions
  setCandidates: (candidates: Candidate[]) => void;
  addCandidate: (candidate: Candidate) => void;
  updateCandidate: (id: string, updates: Partial<Candidate>) => void;
  removeCandidate: (id: string) => void;
  
  // Shortlist Actions
  toggleShortlist: (id: string) => void;
  clearShortlist: () => void;
  
  // Filter Actions
  setFilters: (filters: Partial<CandidateFilters>) => void;
  resetFilters: () => void;
  setSortBy: (sort: SortOption) => void;
  
  // Selection Actions
  selectCandidate: (id: string | null) => void;
  
  // Loading Actions
  setLoading: (loading: boolean) => void;
  setRefreshing: (refreshing: boolean) => void;
  setError: (error: string | null) => void;
  
  // Utility Actions
  reset: () => void;
}

type CandidateStore = CandidateState & CandidateActions;

// ============================================================================
// Initial State
// ============================================================================

const initialState: CandidateState = {
  candidates: [],
  shortlistedIds: new Set(),
  filters: {
    status: 'all',
    minScore: 0,
    jobCategory: 'all',
  },
  sortBy: 'score-desc',
  selectedCandidateId: null,
  isLoading: false,
  isRefreshing: false,
  error: null,
  lastFetchedAt: null,
};

// ============================================================================
// Store Creation
// ============================================================================

export const useCandidateStore = create<CandidateStore>()(
  devtools(
    subscribeWithSelector(
      immer((set) => ({
        ...initialState,

        // Data Actions
        setCandidates: (candidates) =>
          set((state) => {
            state.candidates = candidates;
            state.lastFetchedAt = Date.now();
            state.error = null;
          }, false, 'setCandidates'),

        addCandidate: (candidate) =>
          set((state) => {
            const exists = state.candidates.some((c: Candidate) => c.id === candidate.id);
            if (!exists) {
              state.candidates.unshift(candidate);
            }
          }, false, 'addCandidate'),

        updateCandidate: (id, updates) =>
          set((state) => {
            const index = state.candidates.findIndex((c: Candidate) => c.id === id);
            if (index !== -1) {
              state.candidates[index] = { ...state.candidates[index], ...updates };
            }
          }, false, 'updateCandidate'),

        removeCandidate: (id) =>
          set((state) => {
            state.candidates = state.candidates.filter((c: Candidate) => c.id !== id);
            state.shortlistedIds.delete(id);
          }, false, 'removeCandidate'),

        // Shortlist Actions
        toggleShortlist: (id) =>
          set((state) => {
            if (state.shortlistedIds.has(id)) {
              state.shortlistedIds.delete(id);
            } else {
              state.shortlistedIds.add(id);
            }
          }, false, 'toggleShortlist'),

        clearShortlist: () =>
          set((state) => {
            state.shortlistedIds.clear();
          }, false, 'clearShortlist'),

        // Filter Actions
        setFilters: (filters) =>
          set((state) => {
            state.filters = { ...state.filters, ...filters };
          }, false, 'setFilters'),

        resetFilters: () =>
          set((state) => {
            state.filters = initialState.filters;
          }, false, 'resetFilters'),

        setSortBy: (sort) =>
          set((state) => {
            state.sortBy = sort;
          }, false, 'setSortBy'),

        // Selection Actions
        selectCandidate: (id) =>
          set((state) => {
            state.selectedCandidateId = id;
          }, false, 'selectCandidate'),

        // Loading Actions
        setLoading: (loading) =>
          set((state) => {
            state.isLoading = loading;
          }, false, 'setLoading'),

        setRefreshing: (refreshing) =>
          set((state) => {
            state.isRefreshing = refreshing;
          }, false, 'setRefreshing'),

        setError: (error) =>
          set((state) => {
            state.error = error;
          }, false, 'setError'),

        // Utility Actions
        reset: () => set(initialState, false, 'reset'),
      }))
    ),
    { name: 'CandidateStore' }
  )
);

// ============================================================================
// Selectors (Memoized)
// ============================================================================

// Basic selectors
export const selectCandidates = (state: CandidateStore) => state.candidates;
export const selectShortlistedIds = (state: CandidateStore) => state.shortlistedIds;
export const selectFilters = (state: CandidateStore) => state.filters;
export const selectSortBy = (state: CandidateStore) => state.sortBy;
export const selectIsLoading = (state: CandidateStore) => state.isLoading;
export const selectError = (state: CandidateStore) => state.error;

// Computed selectors
export const selectCandidateById = (id: string) => (state: CandidateStore) =>
  state.candidates.find((c) => c.id === id);

export const selectIsShortlisted = (id: string) => (state: CandidateStore) =>
  state.shortlistedIds.has(id);

export const selectShortlistedCandidates = (state: CandidateStore) =>
  state.candidates.filter((c) => state.shortlistedIds.has(c.id));

export const selectCandidateCount = (state: CandidateStore) => state.candidates.length;

export const selectShortlistCount = (state: CandidateStore) => state.shortlistedIds.size;

// Statistics selector
export const selectStats = (state: CandidateStore) => {
  const candidates = state.candidates;
  const total = candidates.length;
  
  if (total === 0) {
    return {
      total: 0,
      strong: 0,
      partial: 0,
      weak: 0,
      avgScore: 0,
      recentCount: 0,
      byCategory: {},
    };
  }

  const strong = candidates.filter((c) => c.matchScore >= 70).length;
  const partial = candidates.filter((c) => c.matchScore >= 40 && c.matchScore < 70).length;
  const weak = candidates.filter((c) => c.matchScore < 40).length;
  const avgScore = Math.round(
    candidates.reduce((sum, c) => sum + c.matchScore, 0) / total
  );

  // Recent = last 24 hours
  const dayAgo = Date.now() - 24 * 60 * 60 * 1000;
  const recentCount = candidates.filter(
    (c) => new Date(c.appliedDate).getTime() > dayAgo
  ).length;

  // By category
  const byCategory: Record<string, number> = {};
  candidates.forEach((c) => {
    const cat = c.jobCategory || 'General';
    byCategory[cat] = (byCategory[cat] || 0) + 1;
  });

  return { total, strong, partial, weak, avgScore, recentCount, byCategory };
};

// Filtered and sorted candidates selector
export const selectFilteredCandidates = (state: CandidateStore) => {
  const { candidates, filters, sortBy } = state;
  
  // Filter
  let filtered = candidates.filter((candidate) => {
    // Status filter
    if (filters.status && filters.status !== 'all') {
      if (candidate.status !== filters.status) return false;
    }
    
    // Score filter
    if (filters.minScore && candidate.matchScore < filters.minScore) {
      return false;
    }
    if (filters.maxScore && candidate.matchScore > filters.maxScore) {
      return false;
    }
    
    // Experience filter
    if (filters.minExperience && candidate.experience < filters.minExperience) {
      return false;
    }
    
    // Category filter
    if (filters.jobCategory && filters.jobCategory !== 'all') {
      if (candidate.jobCategory !== filters.jobCategory) return false;
    }
    
    // Date range filter
    if (filters.dateStart) {
      const candidateDate = new Date(candidate.appliedDate).getTime();
      const startDate = new Date(filters.dateStart).getTime();
      if (candidateDate < startDate) return false;
    }
    if (filters.dateEnd) {
      const candidateDate = new Date(candidate.appliedDate).getTime();
      const endDate = new Date(filters.dateEnd).setHours(23, 59, 59, 999);
      if (candidateDate > endDate) return false;
    }
    
    // Search filter
    if (filters.search) {
      const search = filters.search.toLowerCase();
      const matchesName = candidate.name.toLowerCase().includes(search);
      const matchesEmail = candidate.email.toLowerCase().includes(search);
      const matchesSkill = candidate.skills.some((s) => s.toLowerCase().includes(search));
      const matchesCategory = candidate.jobCategory.toLowerCase().includes(search);
      if (!matchesName && !matchesEmail && !matchesSkill && !matchesCategory) {
        return false;
      }
    }
    
    return true;
  });
  
  // Sort
  const sorted = [...filtered].sort((a, b) => {
    switch (sortBy) {
      case 'score-desc':
        return b.matchScore - a.matchScore;
      case 'score-asc':
        return a.matchScore - b.matchScore;
      case 'date-newest':
        return new Date(b.appliedDate).getTime() - new Date(a.appliedDate).getTime();
      case 'date-oldest':
        return new Date(a.appliedDate).getTime() - new Date(b.appliedDate).getTime();
      case 'name-asc':
        return a.name.localeCompare(b.name);
      case 'name-desc':
        return b.name.localeCompare(a.name);
      case 'experience-desc':
        return b.experience - a.experience;
      case 'experience-asc':
        return a.experience - b.experience;
      default:
        return 0;
    }
  });
  
  return sorted;
};

// Categories selector
export const selectCategories = (state: CandidateStore) => {
  const categories = new Set(state.candidates.map((c) => c.jobCategory || 'General'));
  return ['all', ...Array.from(categories).sort()];
};

// ============================================================================
// Hooks for Common Patterns
// ============================================================================

// Hook to get candidate by ID with auto-update
export function useCandidateById(id: string) {
  return useCandidateStore(selectCandidateById(id));
}

// Hook to check if candidate is shortlisted
export function useIsShortlisted(id: string) {
  return useCandidateStore(selectIsShortlisted(id));
}

// Hook for filtered candidates
export function useFilteredCandidates() {
  return useCandidateStore(selectFilteredCandidates);
}

// Hook for stats
export function useCandidateStats() {
  return useCandidateStore(selectStats);
}
