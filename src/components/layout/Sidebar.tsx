import { NavLink } from 'react-router-dom'
import { motion } from 'framer-motion'
import {
  LayoutDashboard,
  Search,
  ClipboardList,
  FileEdit,
  Settings,
  ExternalLink,
  Users,
  Star,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { useAuthStore } from '@/store/authStore'

const navigation = [
  { name: 'Dashboard', description: 'Overview & Analytics', href: '/dashboard', icon: LayoutDashboard },
  { name: 'AI Search', description: 'AI-Powered Search', href: '/ai-assistant', icon: Search },
  { name: 'Candidates', description: 'All candidates', href: '/candidates', icon: Users },
  { name: 'Shortlist', description: 'Shortlisted candidates', href: '/shortlist', icon: Star },
  { name: 'Search Reports', description: 'Previous searches & results', href: '/search-reports', icon: ClipboardList },
  { name: 'JD Builder', description: 'AI Job Descriptions', href: '/jd-builder', icon: FileEdit },
  { name: 'Settings', description: 'Preferences', href: '/settings', icon: Settings },
]

export default function Sidebar() {
  const user = useAuthStore((state) => state.user)
  return (
    <div className="w-64 brand-gradient flex flex-col relative">
      {/* Top accent line */}
      <div className="brand-accent-line w-full" />

      {/* Logo — AI Recruiter brand */}
      <div className="h-16 flex items-center px-5 border-b border-white/10">
        <NavLink to="/dashboard" className="flex items-center gap-3 group">
          <div className="h-10 w-10 flex-shrink-0 bg-white/10 backdrop-blur-sm rounded-xl flex items-center justify-center p-1 ring-1 ring-white/20 group-hover:ring-white/40 transition-all">
            <img
              src="/effortz-logo.png"
              alt="AI Recruiter"
              className="h-8 w-8 object-contain"
            />
          </div>
          <div className="min-w-0">
            <h1 className="text-[15px] font-bold text-white leading-tight tracking-tight">
              AI Recruiter
            </h1>
            <p className="text-[10px] font-medium text-sky-300 tracking-wider uppercase">
              Smart Hiring Platform
            </p>
          </div>
        </NavLink>
      </div>

      {/* Navigation */}
      <nav className="flex-1 px-3 py-4 space-y-0.5 overflow-y-auto">
        {navigation.map((item, index) => (
          <motion.div
            key={item.name}
            initial={{ opacity: 0, x: -20 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ delay: index * 0.05 }}
          >
            <NavLink
              to={item.href}
              className={({ isActive }) =>
                cn(
                  'flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm transition-all relative group',
                  isActive
                    ? 'bg-white/15 text-white shadow-lg shadow-black/10 backdrop-blur-sm'
                    : 'text-slate-300 hover:bg-white/8 hover:text-white'
                )
              }
            >
              {({ isActive }) => (
                <>
                  {isActive && (
                    <div className="absolute left-0 top-1/2 -translate-y-1/2 w-[3px] h-5 bg-sky-400 rounded-r-full" />
                  )}
                  <div className={cn(
                    'w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0',
                    isActive ? 'bg-sky-500/20' : 'bg-white/5 group-hover:bg-white/10'
                  )}>
                    <item.icon className={cn('w-[16px] h-[16px]', isActive ? 'text-sky-300' : 'text-slate-400 group-hover:text-slate-300')} />
                  </div>
                  <div className="min-w-0">
                    <span className={cn('block text-sm font-medium leading-tight truncate', isActive ? 'text-white' : '')}>
                      {item.name}
                    </span>
                    <span className="block text-[10px] text-slate-500 truncate leading-tight mt-0.5">
                      {item.description}
                    </span>
                  </div>
                </>
              )}
            </NavLink>
          </motion.div>
        ))}
      </nav>

      {/* User info at bottom */}
      <div className="p-3 border-t border-white/10">
        {user && (
          <div className="flex items-center gap-3 px-3 py-2">
            <div className="w-8 h-8 rounded-full bg-sky-500/20 flex items-center justify-center text-sky-300 text-sm font-bold flex-shrink-0">
              {(user.firstName || user.name || user.username || 'U').charAt(0).toUpperCase()}
            </div>
            <div className="min-w-0 flex-1">
              <p className="text-xs font-semibold text-slate-200 truncate">{user.firstName || user.name || user.username || 'User'}</p>
              <p className="text-[10px] text-slate-500 truncate">{user.email || ''}</p>
            </div>
          </div>
        )}
        <div className="mt-2 flex items-center justify-center gap-1.5">
          <span className="text-[9px] text-slate-500">Powered by</span>
          <a href="https://effortz.com" target="_blank" rel="noopener noreferrer" className="text-[10px] font-semibold text-slate-400 hover:text-sky-300 transition-colors flex items-center gap-1">
            effortz.com <ExternalLink className="w-2.5 h-2.5" />
          </a>
        </div>
      </div>
    </div>
  )
}
