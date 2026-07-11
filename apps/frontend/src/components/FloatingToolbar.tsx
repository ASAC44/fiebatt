import { Fragment, type ReactNode } from 'react'
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { motion } from 'motion/react'
import { useAuth } from '../lib/useAuth'
import './FloatingToolbar.css'

type NavItem = {
  id: string
  label: string
  icon: ReactNode
}

function IrisToolbarIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden>
      <circle cx="12" cy="12" r="9" stroke="currentColor" />
      <circle cx="12" cy="12" r="2.35" stroke="currentColor" />
      <path d="M12 3.2c4.05 0 7.93 3 8.74 7.2H16.6c-.34-2.7-1.63-4.92-4.6-5.62Z" fill="currentColor" stroke="currentColor" strokeLinejoin="round" />
      <path d="M20.8 12c0 4.05-3 7.93-7.2 8.74V16.6c2.7-.34 4.92-1.63 5.62-4.6Z" fill="currentColor" stroke="currentColor" strokeLinejoin="round" />
      <path d="M12 20.8c-4.05 0-7.93-3-8.74-7.2h4.14c.34 2.7 1.63 4.92 4.6 5.62Z" fill="currentColor" stroke="currentColor" strokeLinejoin="round" />
      <path d="M3.2 12c0-4.05 3-7.93 7.2-8.74V7.4c-2.7.34-4.92 1.63-5.62 4.6Z" fill="currentColor" stroke="currentColor" strokeLinejoin="round" />
    </svg>
  )
}

function AboutIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden>
      <path d="M7 3.75h7.4l3.85 3.93v12.57H7z" stroke="currentColor" strokeLinejoin="round" />
      <path d="M14.4 3.75v3.9h3.85" stroke="currentColor" strokeLinejoin="round" />
      <path d="M9.45 11.1h5.3M9.45 14.1h5.3M9.45 17.1h3.8" stroke="currentColor" strokeLinecap="round" />
    </svg>
  )
}

function FeaturesIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden>
      <rect x="6.1" y="6.1" width="3.2" height="3.2" rx="0.65" stroke="currentColor" />
      <rect x="14.7" y="6.1" width="3.2" height="3.2" rx="0.65" stroke="currentColor" />
      <rect x="6.1" y="14.7" width="3.2" height="3.2" rx="0.65" stroke="currentColor" />
      <rect x="14.7" y="14.7" width="3.2" height="3.2" rx="0.65" stroke="currentColor" />
      <path d="M9.3 7.7h5.4M9.3 16.3h5.4M7.7 9.3v5.4M16.3 9.3v5.4" stroke="currentColor" strokeLinecap="round" />
    </svg>
  )
}

function TerminalIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden>
      <rect x="3.5" y="5.5" width="17" height="13" rx="2.8" stroke="currentColor" />
      <path d="m7.4 10 2.35 2.15L7.4 14.3" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M11.9 14.15h4.7" stroke="currentColor" strokeLinecap="round" />
      <path d="M3.9 8.5h16.2" stroke="currentColor" strokeLinecap="round" />
    </svg>
  )
}

function GoogleGlyph({ size = 13 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 48 48" aria-hidden style={{ display: 'block' }}>
      <path fill="#FFC107" d="M43.6 20.5H42V20H24v8h11.3C33.9 32.6 29.4 36 24 36c-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.8 1.1 7.9 3l5.7-5.7C34 6.1 29.3 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.2-.1-2.3-.4-3.5z" />
      <path fill="#FF3D00" d="M6.3 14.7l6.6 4.8C14.5 16 18.9 13 24 13c3.1 0 5.8 1.1 7.9 3l5.7-5.7C34 6.1 29.3 4 24 4 16.3 4 9.7 8.3 6.3 14.7z" />
      <path fill="#4CAF50" d="M24 44c5.2 0 9.9-2 13.4-5.2l-6.2-5.2C29.2 35 26.7 36 24 36c-5.4 0-9.9-3.4-11.3-8.1l-6.5 5C9.6 39.6 16.2 44 24 44z" />
      <path fill="#1976D2" d="M43.6 20.5H42V20H24v8h11.3c-.8 2.2-2.2 4.1-4.1 5.5l6.2 5.2C41.8 35.8 44 30.3 44 24c0-1.2-.1-2.3-.4-3.5z" />
    </svg>
  )
}

const items: NavItem[] = [
  { id: 'top', label: 'Landing', icon: <IrisToolbarIcon /> },
  { id: 'about', label: 'About', icon: <AboutIcon /> },
  { id: 'features', label: 'Workflow', icon: <FeaturesIcon /> },
  { id: 'agents', label: 'Agents', icon: <TerminalIcon /> },
]

export default function FloatingToolbar() {
  const navigate = useNavigate()
  const { status, signInWithGoogle } = useAuth()
  const [activeId, setActiveId] = useState('top')
  const [indicator, setIndicator] = useState({ left: 8, width: 58 })
  const itemRefs = useRef<Record<string, HTMLButtonElement | null>>({})
  const toolbarRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    const updateActive = () => {
      if (window.scrollY < window.innerHeight * 0.35) {
        setActiveId('top')
        return
      }

      const ids = ['about', 'features', 'agents']
      const viewportAnchor = window.innerHeight * 0.3
      let current = ids[0]

      ids.forEach((id) => {
        const el = document.getElementById(id)
        if (!el) return
        const rect = el.getBoundingClientRect()
        if (rect.top <= viewportAnchor) {
          current = id
        }
      })

      setActiveId(current)
    }

    updateActive()
    window.addEventListener('scroll', updateActive, { passive: true })
    window.addEventListener('resize', updateActive)
    return () => {
      window.removeEventListener('scroll', updateActive)
      window.removeEventListener('resize', updateActive)
    }
  }, [])

  useLayoutEffect(() => {
    const target = itemRefs.current[activeId]
    const toolbar = toolbarRef.current
    if (!target || !toolbar) return
    const toolbarRect = toolbar.getBoundingClientRect()
    const targetRect = target.getBoundingClientRect()
    const targetCenter = targetRect.left - toolbarRect.left + targetRect.width / 2
    setIndicator({
      left: targetCenter - targetRect.width / 2,
      width: targetRect.width,
    })
  }, [activeId])

  useEffect(() => {
    const onResize = () => {
      const target = itemRefs.current[activeId]
      const toolbar = toolbarRef.current
      if (!target || !toolbar) return
      const toolbarRect = toolbar.getBoundingClientRect()
      const targetRect = target.getBoundingClientRect()
      const targetCenter = targetRect.left - toolbarRect.left + targetRect.width / 2
      setIndicator({
        left: targetCenter - targetRect.width / 2,
        width: targetRect.width,
      })
    }

    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [activeId])

  const cta = useMemo(() => {
    if (status === 'authed') {
      return {
        label: 'studio',
        onClick: () => navigate('/projects'),
        variant: 'studio' as const,
      }
    }

    return {
      label: status === 'loading' ? 'loading' : 'continue with google',
      onClick: () => {
        if (status !== 'loading') void signInWithGoogle()
      },
      variant: 'auth' as const,
    }
  }, [navigate, signInWithGoogle, status])

  const scrollToSection = (id: string) => {
    if (id === 'top') {
      window.scrollTo({ top: 0, behavior: 'smooth' })
      return
    }
    const section = document.getElementById(id)
    if (!section) return
    const absoluteTop = window.scrollY + section.getBoundingClientRect().top - 112
    window.scrollTo({ top: Math.max(0, absoluteTop), behavior: 'smooth' })
  }

  return (
    <motion.div
      data-intro="nav"
      className="floating-toolbar-wrap"
      initial={false}
    >
      <div className="floating-toolbar-ambient" />

      <div ref={toolbarRef} className="floating-toolbar" data-theme="dark">
        <motion.div
          className="floating-toolbar-indicator"
          animate={{ left: indicator.left, width: indicator.width }}
          transition={{ duration: 0.55, ease: [0.34, 1.2, 0.64, 1] }}
        >
          <div className="floating-toolbar-indicator-glow" />
          <div className="floating-toolbar-indicator-clip">
            <div className="floating-toolbar-indicator-ring" />
          </div>
          <div className="floating-toolbar-indicator-inner" />
        </motion.div>

        {items.map((item, index) => (
          <Fragment key={item.id}>
            <button
              ref={(node) => { itemRefs.current[item.id] = node }}
              className="floating-toolbar-button"
              data-active={activeId === item.id}
              type="button"
              onClick={() => scrollToSection(item.id)}
              aria-label={item.label}
              title={item.label}
            >
              {item.icon}
            </button>
            {index < items.length - 1 ? <div className="floating-toolbar-divider" /> : null}
          </Fragment>
        ))}

        <div className="floating-toolbar-divider floating-toolbar-divider-cta" />

        <button
          type="button"
          className="floating-toolbar-cta"
          data-variant={cta.variant}
          onClick={cta.onClick}
          disabled={status === 'loading'}
          aria-label={cta.variant === 'studio' ? 'Open studio' : 'Continue with Google'}
        >
          {cta.variant === 'auth' && status !== 'loading' ? <GoogleGlyph /> : null}
          <span>{cta.label}</span>
        </button>
      </div>
    </motion.div>
  )
}
