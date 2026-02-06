import { NavLink } from 'react-router-dom'
import { motion } from 'framer-motion'
import {
  LayoutDashboard,
  Users,
  Star,
  Settings,
  Briefcase,
  Mail,
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
  { name: 'Job Descriptions', href: '/jobs', icon: Briefcase },
  { name: 'Shortlist', href: '/shortlist', icon: Star },
  { name: 'Email Integration', href: '/email-integration', icon: Mail },
  { name: 'Setup', href: '/setup', icon: Wrench },
  { name: 'Settings', href: '/settings', icon: Settings },
]

export default function Sidebar() {
  return (
    <div className="w-64 bg-white border-r border-gray-200 flex flex-col">
      {/* Logo */}
      <div className="h-16 flex items-center px-6 border-b border-gray-200">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 bg-primary-600 rounded-xl flex items-center justify-center">
            <Briefcase className="w-6 h-6 text-white" />
          </div>
          <div>
            <h1 className="text-lg font-bold text-gray-900">AI Recruiter</h1>
            <p className="text-xs text-gray-500">UAE Edition</p>
          </div>
        </div>
      </div>

      {/* Navigation */}
      <nav className="flex-1 px-3 py-6 space-y-1">
        {navigation.map((item, index) => (
          <motion.div
            key={item.name}
            initial={{ opacity: 0, x: -20 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ delay: index * 0.1 }}
          >
            <NavLink
              to={item.href}
              className={({ isActive }) =>
                cn(
                  'flex items-center gap-3 px-4 py-3 rounded-lg text-sm font-medium transition-all relative',
                  isActive
                    ? item.highlight
                      ? 'bg-gradient-to-r from-primary-500 to-purple-600 text-white shadow-lg'
                      : 'bg-primary-50 text-primary-700 shadow-sm'
                    : item.highlight
                    ? 'text-gray-700 hover:bg-gradient-to-r hover:from-primary-50 hover:to-purple-50'
                    : 'text-gray-700 hover:bg-gray-50 hover:text-gray-900'
                )
              }
            >
              {({ isActive }) => (
                <>
                  <item.icon className={cn('w-5 h-5', isActive && !item.highlight && 'text-primary-600', isActive && item.highlight && 'text-white')} />
                  {item.name}
                  {item.highlight && !isActive && (
                    <span className="ml-auto px-2 py-0.5 bg-gradient-to-r from-primary-500 to-purple-600 text-white text-xs rounded-full font-semibold">
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
      <div className="p-4 border-t border-gray-200">
        <div className="bg-gradient-to-r from-primary-50 to-purple-50 rounded-lg p-4">
          <p className="text-xs font-semibold text-gray-900 mb-1">Need Help?</p>
          <p className="text-xs text-gray-600 mb-2">Check our documentation</p>
          <button 
            onClick={() => window.open('https://github.com/yourusername/ai-recruiter-platform', '_blank')}
            className="text-xs font-medium text-primary-600 hover:text-primary-700"
          >
            View Docs →
          </button>
        </div>
      </div>
    </div>
  )
}
