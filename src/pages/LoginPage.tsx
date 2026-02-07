import { useState, useEffect } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Mail, Lock, Briefcase, Zap, TrendingUp, CheckCircle, ArrowRight, Sparkles, Brain, Target, User, AlertCircle, AtSign } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { useAuthStore } from '@/store/authStore'

export default function LoginPage() {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [name, setName] = useState('')
  const [username, setUsername] = useState('')
  const [isLoading, setIsLoading] = useState(false)
  const [activeFeature, setActiveFeature] = useState(0)
  const [isRegistering, setIsRegistering] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [confirmPassword, setConfirmPassword] = useState('')
  const login = useAuthStore((state) => state.login)
  const register = useAuthStore((state) => state.register)

  const features = [
    {
      icon: Brain,
      title: 'AI-Powered Matching',
      description: 'Advanced algorithms analyze candidates against job requirements'
    },
    {
      icon: Zap,
      title: 'Instant Resume Parsing',
      description: 'Extract key information from resumes in seconds'
    },
    {
      icon: Target,
      title: 'Smart Filtering',
      description: 'Find the perfect candidates with intelligent search'
    },
    {
      icon: TrendingUp,
      title: 'Analytics Dashboard',
      description: 'Track recruitment metrics and optimize your process'
    }
  ]

  const stats = [
    { value: '10,000+', label: 'Candidates Analyzed' },
    { value: '95%', label: 'Match Accuracy' },
    { value: '5x', label: 'Faster Hiring' },
  ]

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    setIsLoading(true)
    
    try {
      if (isRegistering) {
        // Validate registration
        if (!name || name.trim().length < 2) {
          throw new Error('Full name is required')
        }
        if (!email || !email.includes('@')) {
          throw new Error('Valid email is required')
        }
        if (password.length < 6) {
          throw new Error('Password must be at least 6 characters')
        }
        if (password !== confirmPassword) {
          throw new Error('Passwords do not match')
        }
        
        // Register new account using store function
        await register(email, password, name, username || undefined)
      } else {
        // Validate login
        if (!email || !password) {
          throw new Error('Email/username and password are required')
        }
        // Login using store function (email field accepts email or username)
        await login(email, password)
      }
    } catch (err) {
      console.error(isRegistering ? 'Registration failed:' : 'Login failed:', err)
      setError(err instanceof Error ? err.message : 'Authentication failed')
    } finally {
      setIsLoading(false)
    }
  }

  // Auto-rotate features
  useEffect(() => {
    const interval = setInterval(() => {
      setActiveFeature((prev) => (prev + 1) % features.length)
    }, 4000)
    return () => clearInterval(interval)
  }, [features.length])

  return (
    <div className="min-h-screen bg-gradient-to-br from-primary-50 via-white to-purple-50 flex">
      {/* Left Side - Features & Branding */}
      <div className="hidden lg:flex lg:w-1/2 xl:w-3/5 bg-gradient-to-br from-primary-600 via-primary-700 to-purple-600 p-12 flex-col justify-between relative overflow-hidden">
        {/* Animated background elements */}
        <motion.div
          animate={{
            scale: [1, 1.2, 1],
            rotate: [0, 90, 0],
          }}
          transition={{ duration: 20, repeat: Infinity, ease: "linear" }}
          className="absolute top-0 right-0 w-96 h-96 bg-white/5 rounded-full blur-3xl"
        />
        <motion.div
          animate={{
            scale: [1.2, 1, 1.2],
            rotate: [90, 0, 90],
          }}
          transition={{ duration: 15, repeat: Infinity, ease: "linear" }}
          className="absolute bottom-0 left-0 w-96 h-96 bg-purple-500/10 rounded-full blur-3xl"
        />

        <div className="relative z-10">
          {/* Logo */}
          <motion.div
            initial={{ opacity: 0, y: -20 }}
            animate={{ opacity: 1, y: 0 }}
            className="flex items-center gap-3 mb-12"
          >
            <div className="w-12 h-12 bg-white/20 backdrop-blur-sm rounded-xl flex items-center justify-center">
              <Briefcase className="w-6 h-6 text-white" />
            </div>
            <div>
              <h1 className="text-2xl font-bold text-white">AI Recruiter</h1>
              <p className="text-primary-100 text-sm">UAE Edition</p>
            </div>
          </motion.div>

          {/* Main Headline */}
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.2 }}
            className="mb-12"
          >
            <h2 className="text-5xl font-bold text-white mb-4 leading-tight">
              Find Your Perfect
              <br />
              <span className="text-primary-200">Candidate Match</span>
            </h2>
            <p className="text-primary-100 text-lg">
              Leverage AI to transform your recruitment process and hire top talent faster
            </p>
          </motion.div>

          {/* Rotating Features */}
          <div className="space-y-6 mb-12">
            <AnimatePresence mode="wait">
              <motion.div
                key={activeFeature}
                initial={{ opacity: 0, x: -20 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0, x: 20 }}
                transition={{ duration: 0.5 }}
                className="bg-white/10 backdrop-blur-md rounded-2xl p-6 border border-white/20"
              >
                <div className="flex items-start gap-4">
                  <div className="w-12 h-12 bg-white/20 rounded-xl flex items-center justify-center flex-shrink-0">
                    {(() => {
                      const Icon = features[activeFeature].icon
                      return <Icon className="w-6 h-6 text-white" />
                    })()}
                  </div>
                  <div className="flex-1">
                    <h3 className="text-xl font-semibold text-white mb-2">
                      {features[activeFeature].title}
                    </h3>
                    <p className="text-primary-100">
                      {features[activeFeature].description}
                    </p>
                  </div>
                </div>
              </motion.div>
            </AnimatePresence>

            {/* Feature indicators */}
            <div className="flex gap-2 justify-center">
              {features.map((_, index) => (
                <button
                  key={index}
                  onClick={() => setActiveFeature(index)}
                  className={`h-2 rounded-full transition-all ${
                    index === activeFeature ? 'w-8 bg-white' : 'w-2 bg-white/30'
                  }`}
                />
              ))}
            </div>
          </div>

          {/* All Features Grid */}
          <div className="grid grid-cols-2 gap-4">
            {features.map((feature, index) => (
                <motion.div
                  key={index}
                  initial={{ opacity: 0, scale: 0.8 }}
                  animate={{ opacity: 1, scale: 1 }}
                  transition={{ delay: 0.4 + index * 0.1 }}
                  className="flex items-center gap-3"
                >
                  <CheckCircle className="w-5 h-5 text-primary-200 flex-shrink-0" />
                  <span className="text-white text-sm">{feature.title}</span>
                </motion.div>
              )
            )}
          </div>
        </div>

        {/* Stats Footer */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.6 }}
          className="relative z-10 grid grid-cols-3 gap-8 pt-8 border-t border-white/20"
        >
          {stats.map((stat, index) => (
            <div key={index} className="text-center">
              <div className="text-3xl font-bold text-white mb-1">{stat.value}</div>
              <div className="text-primary-100 text-sm">{stat.label}</div>
            </div>
          ))}
        </motion.div>
      </div>

      {/* Right Side - Login Form */}
      <div className="flex-1 flex items-center justify-center p-4 lg:p-12">
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5 }}
          className="w-full max-w-md"
        >
          {/* Mobile Logo */}
          <div className="lg:hidden text-center mb-8">
            <motion.div
              initial={{ scale: 0 }}
              animate={{ scale: 1 }}
              transition={{ delay: 0.2, type: "spring" }}
              className="inline-flex items-center justify-center w-16 h-16 bg-primary-600 rounded-2xl shadow-lg mb-4"
            >
              <Briefcase className="w-8 h-8 text-white" />
            </motion.div>
            <h1 className="text-3xl font-bold text-gray-900 mb-2">
              AI Recruiter
            </h1>
            <p className="text-gray-600">
              Intelligent candidate matching
            </p>
          </div>

          {/* Login Card */}
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.3, duration: 0.5 }}
            className="bg-white rounded-2xl shadow-large p-8 border border-gray-200"
          >
            <div className="flex items-center gap-2 mb-2">
              <Sparkles className="w-5 h-5 text-primary-600" />
              <h2 className="text-2xl font-semibold text-gray-900">
                {isRegistering ? 'Create Account' : 'Welcome Back'}
              </h2>
            </div>
            <p className="text-gray-600 mb-6">
              {isRegistering ? 'Create your account to get started' : 'Sign in to continue to your dashboard'}
            </p>

            {/* Error Display */}
            {error && (
              <motion.div
                initial={{ opacity: 0, y: -10 }}
                animate={{ opacity: 1, y: 0 }}
                className="mb-4 p-3 bg-red-50 border border-red-200 rounded-lg flex items-center gap-2 text-red-700"
              >
                <AlertCircle className="w-5 h-5 flex-shrink-0" />
                <span className="text-sm">{error}</span>
              </motion.div>
            )}

            <form onSubmit={handleSubmit} className="space-y-5">
              {/* Name field - only for registration */}
              {isRegistering && (
                <div>
                  <label htmlFor="name" className="block text-sm font-medium text-gray-700 mb-2">
                    Full Name <span className="text-red-500">*</span>
                  </label>
                  <div className="relative">
                    <User className="absolute left-3 top-1/2 transform -translate-y-1/2 w-5 h-5 text-gray-400" />
                    <Input
                      id="name"
                      type="text"
                      placeholder="John Doe"
                      value={name}
                      onChange={(e) => { setName(e.target.value); setError(null) }}
                      className="pl-10"
                      required
                    />
                  </div>
                </div>
              )}

              {/* Email field - for both login and registration */}
              <div>
                <label htmlFor="email" className="block text-sm font-medium text-gray-700 mb-2">
                  {isRegistering ? 'Email Address' : 'Email or Username'} {isRegistering && <span className="text-red-500">*</span>}
                </label>
                <div className="relative">
                  <Mail className="absolute left-3 top-1/2 transform -translate-y-1/2 w-5 h-5 text-gray-400" />
                  <Input
                    id="email"
                    type={isRegistering ? "email" : "text"}
                    placeholder={isRegistering ? "recruiter@company.ae" : "email or username"}
                    value={email}
                    onChange={(e) => { setEmail(e.target.value); setError(null) }}
                    className="pl-10"
                    required
                  />
                </div>
              </div>

              {/* Username field - only for registration (optional) */}
              {isRegistering && (
                <div>
                  <label htmlFor="username" className="block text-sm font-medium text-gray-700 mb-2">
                    Username <span className="text-gray-400 text-xs">(optional - for quick login)</span>
                  </label>
                  <div className="relative">
                    <AtSign className="absolute left-3 top-1/2 transform -translate-y-1/2 w-5 h-5 text-gray-400" />
                    <Input
                      id="username"
                      type="text"
                      placeholder="johndoe"
                      value={username}
                      onChange={(e) => { setUsername(e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, '')); setError(null) }}
                      className="pl-10"
                    />
                  </div>
                  <p className="mt-1 text-xs text-gray-500">Only lowercase letters, numbers, and underscores</p>
                </div>
              )}

              <div>
                <label htmlFor="password" className="block text-sm font-medium text-gray-700 mb-2">
                  Password {isRegistering && <span className="text-red-500">*</span>}
                </label>
                <div className="relative">
                  <Lock className="absolute left-3 top-1/2 transform -translate-y-1/2 w-5 h-5 text-gray-400" />
                  <Input
                    id="password"
                    type="password"
                    placeholder="••••••••"
                    value={password}
                    onChange={(e) => { setPassword(e.target.value); setError(null) }}
                    className="pl-10"
                    required
                    minLength={6}
                  />
                </div>
                {isRegistering && (
                  <p className="mt-1 text-xs text-gray-500">Must be at least 6 characters</p>
                )}
              </div>

              {/* Confirm Password - only for registration */}
              {isRegistering && (
                <div>
                  <label htmlFor="confirmPassword" className="block text-sm font-medium text-gray-700 mb-2">
                    Confirm Password
                  </label>
                  <div className="relative">
                    <Lock className="absolute left-3 top-1/2 transform -translate-y-1/2 w-5 h-5 text-gray-400" />
                    <Input
                      id="confirmPassword"
                      type="password"
                      placeholder="••••••••"
                      value={confirmPassword}
                      onChange={(e) => { setConfirmPassword(e.target.value); setError(null) }}
                      className="pl-10"
                      required
                    />
                  </div>
                </div>
              )}

              <div className="flex items-center justify-between">
                <label className="flex items-center">
                  <input
                    type="checkbox"
                    className="w-4 h-4 text-primary-600 border-gray-300 rounded focus:ring-primary-500"
                  />
                  <span className="ml-2 text-sm text-gray-600">Remember me</span>
                </label>
                {!isRegistering && (
                  <button type="button" className="text-sm font-medium text-primary-600 hover:text-primary-700">
                    Forgot password?
                  </button>
                )}
              </div>

              <Button
                type="submit"
                className="w-full group"
                size="lg"
                disabled={isLoading}
              >
                {isLoading ? (
                  isRegistering ? 'Creating account...' : 'Signing in...'
                ) : (
                  <>
                    {isRegistering ? 'Create Account' : 'Sign in'}
                    <ArrowRight className="w-4 h-4 ml-2 group-hover:translate-x-1 transition-transform" />
                  </>
                )}
              </Button>
            </form>

            <div className="mt-6 pt-6 border-t border-gray-200">
              <p className="text-center text-sm text-gray-600">
                {isRegistering ? 'Already have an account?' : "Don't have an account?"}{' '}
                <button 
                  type="button"
                  onClick={() => {
                    setIsRegistering(!isRegistering)
                    setError(null)
                    setPassword('')
                    setConfirmPassword('')
                    setUsername('')
                    setName('')
                  }}
                  className="font-medium text-primary-600 hover:text-primary-700"
                >
                  {isRegistering ? 'Sign in' : 'Create account'}
                </button>
              </p>
            </div>
          </motion.div>

          {/* Footer */}
          <motion.p
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ delay: 0.5 }}
            className="text-center text-sm text-gray-500 mt-6"
          >
            © 2026 AI Recruiter Platform - UAE Edition. All rights reserved.
          </motion.p>
        </motion.div>
      </div>
    </div>
  )
}
