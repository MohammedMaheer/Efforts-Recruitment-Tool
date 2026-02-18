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
    <div className="w-64 bg-white border-r border-gray-200 flex flex-col">
      {/* Logo — Efforts Solutions brand */}
      <div className="h-16 flex items-center px-5 border-b border-gray-100">
        <NavLink to="/dashboard" className="flex items-center gap-3 group">
          <div className="h-9 w-9 flex-shrink-0 bg-blue-700 rounded-lg flex items-center justify-center p-1">
            <img
              src="/effortz-logo.png"
              alt="Efforts Solutions"
              className="h-7 w-7 object-contain"
            />
          </div>
          <div className="min-w-0">
            <h1 className="text-[15px] font-bold text-gray-900 leading-tight tracking-tight group-hover:text-blue-700 transition-colors">
              Efforts Solutions
            </h1>
            <p className="text-[10px] font-medium text-blue-600 tracking-wide uppercase">
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
                  'flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-all relative',
                  isActive
                    ? item.highlight
                      ? 'bg-gradient-to-r from-blue-600 to-blue-800 text-white shadow-md shadow-blue-200'
                      : 'bg-blue-50 text-blue-700 font-semibold'
                    : item.highlight
                    ? 'text-gray-600 hover:bg-blue-50/60 hover:text-blue-700'
                    : 'text-gray-600 hover:bg-gray-50 hover:text-gray-900'
                )
              }
            >
              {({ isActive }) => (
                <>
                  <item.icon className={cn('w-[18px] h-[18px] flex-shrink-0', isActive && !item.highlight && 'text-blue-600', isActive && item.highlight && 'text-white')} />
                  <span className="truncate">{item.name}</span>
                  {item.highlight && !isActive && (
                    <span className="ml-auto px-1.5 py-0.5 bg-blue-600 text-white text-[10px] rounded-full font-semibold leading-none">
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
      <div className="p-3 border-t border-gray-100">
        <div className="bg-gradient-to-br from-blue-50 to-slate-50 rounded-lg p-3">
          <p className="text-[11px] font-semibold text-gray-800 mb-0.5">Need Help?</p>
          <p className="text-[10px] text-gray-500 mb-2">Check setup & configuration</p>
          <NavLink 
            to="/setup"
            className="text-[11px] font-semibold text-blue-600 hover:text-blue-800 transition-colors"
          >
            Setup Guide →
          </NavLink>
        </div>
      </div>
    </div>
  )
}
