import { NavLink } from 'react-router-dom'
import { motion } from 'framer-motion'
import {
  LayoutDashboard,
  Users,
  Star,
  Settings,
  Sparkles,
  BarChart3,
  Wrench,
  ExternalLink,
} from 'lucide-react'
import { cn } from '@/lib/utils'

const navigation = [
  { name: 'Dashboard', href: '/dashboard', icon: LayoutDashboard },
  { name: 'AI Assistant', href: '/ai-assistant', icon: Sparkles, highlight: true },
  { name: 'Analytics', href: '/analytics', icon: BarChart3, highlight: true },
  { name: 'Candidates', href: '/candidates', icon: Users },
  { name: 'Shortlist', href: '/shortlist', icon: Star },
  { name: 'Setup', href: '/setup', icon: Wrench },
  { name: 'Settings', href: '/settings', icon: Settings },
]

export default function Sidebar() {
  return (
    <div className="w-64 brand-gradient flex flex-col relative">
      {/* Top accent line */}
      <div className="brand-accent-line w-full" />

      {/* Logo — Efforts Solutions brand */}
      <div className="h-16 flex items-center px-5 border-b border-white/10">
        <NavLink to="/dashboard" className="flex items-center gap-3 group">
          <div className="h-10 w-10 flex-shrink-0 bg-white/10 backdrop-blur-sm rounded-xl flex items-center justify-center p-1 ring-1 ring-white/20 group-hover:ring-white/40 transition-all">
            <img
              src="/effortz-logo.png"
              alt="Efforts Solutions"
              className="h-8 w-8 object-contain"
            />
          </div>
          <div className="min-w-0">
            <h1 className="text-[15px] font-bold text-white leading-tight tracking-tight">
              Efforts Solutions
            </h1>
            <p className="text-[10px] font-medium text-sky-300 tracking-wider uppercase">
              Smart Hiring Platform
            </p>
          </div>
        </NavLink>
      </div>

      {/* Navigation */}
      <nav className="flex-1 px-3 py-4 space-y-0.5 overflow-y-auto">
        <p className="px-3 mb-2 text-[10px] font-semibold text-slate-400 uppercase tracking-widest">Menu</p>
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
                  'flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-all relative',
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
                  <item.icon className={cn('w-[18px] h-[18px] flex-shrink-0', isActive ? 'text-sky-300' : 'text-slate-400')} />
                  <span className="truncate">{item.name}</span>
                  {item.highlight && !isActive && (
                    <span className="ml-auto px-1.5 py-0.5 bg-sky-500/20 text-sky-300 text-[10px] rounded-full font-semibold leading-none ring-1 ring-sky-400/30">
                      New
                    </span>
                  )}
                </>
              )}
            </NavLink>
          </motion.div>
        ))}
      </nav>

      {/* Footer */}
      <div className="p-3 border-t border-white/10">
        <div className="bg-white/5 backdrop-blur-sm rounded-lg p-3 ring-1 ring-white/10">
          <p className="text-[11px] font-semibold text-slate-200 mb-0.5">Need Help?</p>
          <p className="text-[10px] text-slate-400 mb-2">Check setup & configuration</p>
          <NavLink 
            to="/setup"
            className="text-[11px] font-semibold text-sky-300 hover:text-sky-200 transition-colors"
          >
            Setup Guide →
          </NavLink>
        </div>
        <div className="mt-3 flex items-center justify-center gap-1.5">
          <span className="text-[9px] text-slate-500">Powered by</span>
          <a href="https://effortz.com" target="_blank" rel="noopener noreferrer" className="text-[10px] font-semibold text-slate-400 hover:text-sky-300 transition-colors flex items-center gap-1">
            effortz.com <ExternalLink className="w-2.5 h-2.5" />
          </a>
        </div>
      </div>
    </div>
  )
}
