import { useState, useEffect, useRef, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { motion, AnimatePresence, useScroll, useTransform } from 'motion/react'
import gsap from 'gsap'
import Lenis from 'lenis'
import MetallicPaint from './components/MetallicPaint'
import Noise from './components/Noise'
import LogoLoop, { type LogoLoopItem } from './components/LogoLoop'
import ScrollFrames from './components/ScrollFrames'
import ASCIIText from './components/ASCIIText'
import CardSwapShowcase from './components/CardSwapShowcase'
import FloatingToolbar from './components/FloatingToolbar'
import SmokeOrbBackground from './components/SmokeOrbBackground'
import { useAuth } from './lib/useAuth'

const IRIS_SVG = '/iris-logo.svg'
const IRIS_METAL_TINT = '#badcff'
const IRIS_WORDMARK_TEXT = 'iris.'
const IRIS_WORDMARK_MASK = {
  fontFamily: 'Sentient, Georgia, serif',
  fontSize: 344,
  fontWeight: 300,
  letterSpacing: 26,
  paddingX: 0,
  paddingY: 56,
}

const FLOWER_METALLIC_PROPS = {
  seed: 117,
  scale: 4,
  patternSharpness: 1,
  noiseScale: 0.5,
  speed: 0.16,
  startTime: 1400,
  liquid: 0.8,
  brightness: 2.2,
  contrast: 0.5,
  refraction: 0.015,
  blur: 0.012,
  chromaticSpread: 2,
  fresnel: 1.2,
  waveAmplitude: 1,
  distortion: 0.8,
  contour: 0.25,
  lightColor: '#f4fbff',
  darkColor: '#000000',
  tintColor: IRIS_METAL_TINT,
} as const

const WORDMARK_METALLIC_PROPS = {
  seed: 42,
  scale: 4,
  patternSharpness: 1,
  noiseScale: 0.5,
  speed: 0.2,
  startTime: 0,
  liquid: 0.8,
  brightness: 2.2,
  contrast: 0.5,
  refraction: 0.015,
  blur: 0.012,
  chromaticSpread: 2,
  fresnel: 1.2,
  waveAmplitude: 1,
  distortion: 0.8,
  contour: 0.25,
  lightColor: '#f4fbff',
  darkColor: '#000000',
  tintColor: IRIS_METAL_TINT,
} as const

// motion design system (lottiefiles + framerlabs + palmer)
//
// archetype: PREMIUM — elegant, minimal, luxury
// signature easing: cubic-bezier(0.4, 0, 0.2, 1) — 80% of animations
// enter > exit rule: entrances 30-50% longer than exits
// three layers: primary + secondary + ambient
// never opacity-only for important state changes
//
const EASE = {
  // industry standards
  premium: [0.4, 0, 0.2, 1] as const,       // material design 3 — default
  emphasized: [0.05, 0.7, 0.1, 1] as const,  // MD3 — entrances, attention
  accelerate: [0.3, 0, 1, 1] as const,       // MD3 — exits, dismissals
  // framerlabs
  popUp: [0, 0, 0.39, 2.99] as const,        // natural overshoot, no elastic
  settle: [0.25, 0.46, 0.45, 0.94] as const, // smooth settle
  // palmer
  sharp: [0.82, 0.08, 0.29, 1] as const,
}

// duration palette (premium archetype: 350-600ms)
const DUR = {
  micro: 0.1,     // tooltip, feedback
  quick: 0.18,    // button press, toggle
  standard: 0.35, // card enter, icon
  slow: 0.5,      // modal, page transition
  dramatic: 0.8,  // hero reveal
}

// stagger budget: dramatic 100-200ms, total under 600ms
const STAGGER = {
  micro: 0.03,    // list items
  standard: 0.07, // cards, panels
  dramatic: 0.15, // hero sections
}

const SPRING = {
  magnetic: { type: 'spring' as const, stiffness: 1000, damping: 100 },
}

// ── loader ──────────────────────────────────────────────────────────

const ASCII_IRIS = `                               ....
                           ,;;'''';;,                    ,;;;;,
                 ,        ;;'      \`;;,               .,;;;'   ;
              ,;;;       ;;          \`;;,';;;,.     ,%;;'     '
            ,;;,;;       ;;         ,;\`;;;, \`;::.  %%;'
           ;;;,;;;       \`'       ,;;; ;;,;;, \`::,%%;'
           ;;;,;;;,          .,%%%%%'% ;;;;,;;   %;;;
 ,%,.      \`;;;,;;;,    .,%%%%%%%%%'%; ;;;;;,;;  %;;;
;,\`%%%%%%%%%%\`;;,;;'%%%%%%%%%%%%%'%%'  \`;;;;;,;, %;;;
;;;,\`%%%%%%%%%%%,; ..\`%%%%%%%%;'%%%'    \`;;;;,;; %%;;
 \`;;;;;,\`%%%%%,;;/, .. \`"""'',%%%%%      \`;;;;;; %%;;,
    \`;;;;;;;,;;/////,.    ,;%%%%%%%        \`;;;;,\`%%;;
           ;;;/%%%%,%///;;;';%%%%%%,          \`;;;%%;;,
          ;;;/%%%,%%%%%/;;;';;'%%%%%,             \`%%;;
         .;;/%%,%%%%%//;;'  ;;;'%%%%%,             %%;;,
         ;;//%,%%%%//;;;'   \`;;;;'%%%%             \`%;;;
         ;;//%,%//;;;;'      \`;;;;'%%%              %;;;,
         \`;\;//,/;;;'          \`;;;'%%'              \`%;;;
           \`;;;;'               \`;\`%'                \`;;;;
                                  '      .,,,.        \`;;;;
                                      ,;;;;;;;;;;,     \`;;;;
                                     ;;;'    ;;;,;;,    \`;;;;
                                     ;;;      ;;;;,;;.   \`;;;;
                                      \`;;      ;;;;;,;;   ;;;;
                                        \`'      \`;;;;,;;  ;;;;
                                                   \`;;,;, ;;;;
                                                      ;;, ;;;;
                                                        ';;;;;
                                                         ;;;;;
                                                        .;;;;'
                                                       .;;;;'
                                                      ;;;;;'
                                                     ,;;;;'`

const SCRAMBLE = '.·:;+*%#@'

function AsciiScramble({ text }: { text: string }) {
  const [display, setDisplay] = useState('')
  const f = useRef<number>(0)
  useEffect(() => {
    const lines = text.split('\n'), rows = lines.length, cols = Math.max(...lines.map(l => l.length))
    const target = lines.map(l => l.padEnd(cols, ' ').split(''))
    const content: [number, number][] = []
    for (let r = 0; r < rows; r++) for (let c = 0; c < cols; c++) if (target[r][c] !== ' ') content.push([r, c])
    const pts = content.map(([r, c]) => ({ tr: r, tc: c, sr: r + Math.round((Math.random() - .5) * 12), sc: c + Math.round((Math.random() - .5) * 20) }))
    let tick = 0; const total = 34
    function render() {
      const p = tick / total
      const grid = Array.from({ length: rows }, () => Array(cols).fill(' '))
      for (const pt of pts) {
        const r = Math.round(pt.sr + (pt.tr - pt.sr) * p), c = Math.round(pt.sc + (pt.tc - pt.sc) * p)
        if (r < 0 || r >= rows || c < 0 || c >= cols) continue
        grid[r][c] = p > .7 ? target[pt.tr][pt.tc] : Math.random() < p * .3 ? target[pt.tr][pt.tc] : SCRAMBLE[Math.floor(Math.random() * SCRAMBLE.length)]
      }
      setDisplay(grid.map(r => r.join('')).join('\n'))
      tick++; if (tick <= total) f.current = requestAnimationFrame(render); else setDisplay(text)
    }
    const t = setTimeout(() => { f.current = requestAnimationFrame(render) }, 80)
    return () => { clearTimeout(t); cancelAnimationFrame(f.current) }
  }, [text])
  return <pre style={{ fontFamily: 'var(--font-mono)', fontSize: '5px', lineHeight: 1.1, color: 'rgba(255,255,255,0.7)', userSelect: 'none', whiteSpace: 'pre' }}>{display}</pre>
}

function Loader({ onComplete }: { onComplete: () => void }) {
  const [progress, setProgress] = useState(0)

  // scroll lock during loader (framerlabs pattern)
  useEffect(() => {
    const orig = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    window.scrollTo(0, 0)
    return () => { document.body.style.overflow = orig }
  }, [])

  useEffect(() => {
    const i = setInterval(() => {
      setProgress(p => { if (p >= 100) { clearInterval(i); setTimeout(onComplete, 120); return 100 } return Math.min(p + (p < 70 ? 6 : p < 92 ? 4 : 2), 100) })
    }, 20); return () => clearInterval(i)
  }, [onComplete])
  return (
    <motion.div exit={{ opacity: 0 }} transition={{ duration: DUR.standard, ease: EASE.accelerate }}
      style={{ position: 'fixed', inset: 0, zIndex: 100, background: '#000', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: '32px' }}>
      <AsciiScramble text={ASCII_IRIS} />
      <div style={{ width: '120px', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '12px' }}>
        <div style={{ width: '100%', height: '1px', background: 'rgba(255,255,255,0.1)', position: 'relative', overflow: 'hidden' }}>
          <div style={{ position: 'absolute', inset: 0, right: 'auto', width: `${progress}%`, background: 'rgba(255,255,255,0.3)' }} />
        </div>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'rgba(255,255,255,0.2)', letterSpacing: '0.2em' }}>{progress}%</span>
      </div>
    </motion.div>
  )
}

// ── magnetic wrapper (framerlabs pattern) ────────────────────────────

function Magnetic({ children, intensity = 0.3 }: { children: React.ReactNode; intensity?: number }) {
  const ref = useRef<HTMLDivElement>(null)
  const [pos, setPos] = useState({ x: 0, y: 0 })

  const handleMove = (e: React.MouseEvent) => {
    const el = ref.current
    if (!el) return
    const rect = el.getBoundingClientRect()
    const cx = rect.left + rect.width / 2
    const cy = rect.top + rect.height / 2
    setPos({ x: (e.clientX - cx) * intensity, y: (e.clientY - cy) * intensity })
  }

  const handleLeave = () => setPos({ x: 0, y: 0 })

  return (
    <motion.div
      ref={ref}
      onMouseMove={handleMove}
      onMouseLeave={handleLeave}
      animate={{ x: pos.x, y: pos.y }}
      transition={{ type: 'spring', stiffness: 1000, damping: 100 }}
      style={{ display: 'inline-block' }}
    >
      {children}
    </motion.div>
  )
}

// ── pill nav ────────────────────────────────────────────────────────

function PillNav({ onStudio }: { onStudio: () => void }) {
  const [show, setShow] = useState(false)
  const navItems = [
    { label: 'Product', href: '#about' },
    { label: 'Workflow', href: '#features' },
    { label: 'Agents', href: '#agents' },
  ]
  useEffect(() => {
    setShow(true) // always visible
    const fn = () => setShow(true)
    window.addEventListener('scroll', fn, { passive: true }); return () => window.removeEventListener('scroll', fn)
  }, [])
  return (
    <AnimatePresence>
      {show && (
        <motion.nav data-intro="nav" initial={{ opacity: 0, y: 40 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 40 }} transition={{ duration: DUR.slow, ease: EASE.premium }}
          style={{ position: 'fixed', bottom: '24px', left: 0, right: 0, marginLeft: 'auto', marginRight: 'auto', width: 'fit-content', zIndex: 50, display: 'flex', alignItems: 'center', gap: '24px', padding: '10px 12px 10px 24px', background: 'rgba(255,255,255,0.04)', backdropFilter: 'blur(24px)', border: '1px solid rgba(255,255,255,0.08)', borderRadius: '9999px', fontFamily: 'var(--font-mono)', fontSize: '12px' }}>
          {navItems.map(item => (
            <a key={item.href} href={item.href} style={{ color: 'rgba(255,255,255,0.4)', textDecoration: 'none', transition: 'color 0.2s' }}
              onMouseEnter={e => (e.currentTarget.style.color = '#fff')} onMouseLeave={e => (e.currentTarget.style.color = 'rgba(255,255,255,0.4)')}>{item.label}</a>
          ))}
          <AuthChip />
          <Magnetic intensity={0.25}>
            <button onClick={onStudio} style={{ padding: '8px 20px', background: 'linear-gradient(135deg, rgba(248,250,252,0.98) 0%, rgba(224,232,240,0.96) 48%, rgba(255,255,255,0.98) 100%)', color: '#000', border: '1px solid rgba(255,255,255,0.6)', borderRadius: '9999px', boxShadow: '0 0 0 1px rgba(255,255,255,0.24) inset, 0 14px 38px rgba(186,220,255,0.16)', fontFamily: 'var(--font-body)', fontSize: '13px', fontWeight: 400, letterSpacing: '0.04em', cursor: 'pointer', transition: 'transform 0.2s, box-shadow 0.2s' }}
              onMouseEnter={e => (e.currentTarget.style.boxShadow = '0 0 0 1px rgba(255,255,255,0.3) inset, 0 16px 44px rgba(186,220,255,0.22)')} onMouseLeave={e => (e.currentTarget.style.boxShadow = '0 0 0 1px rgba(255,255,255,0.24) inset, 0 14px 38px rgba(186,220,255,0.16)')}>Open Studio</button>
          </Magnetic>
        </motion.nav>
      )}
    </AnimatePresence>
  )
}

// ── auth chip ────────────────────────────────────────────────────────
// compact sign-in / signed-in indicator. sits in the pill nav + hero top bar.

function GoogleGlyph({ size = 12 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 48 48" aria-hidden style={{ display: 'block' }}>
      <path fill="#FFC107" d="M43.6 20.5H42V20H24v8h11.3C33.9 32.6 29.4 36 24 36c-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.8 1.1 7.9 3l5.7-5.7C34 6.1 29.3 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.2-.1-2.3-.4-3.5z" />
      <path fill="#FF3D00" d="M6.3 14.7l6.6 4.8C14.5 16 18.9 13 24 13c3.1 0 5.8 1.1 7.9 3l5.7-5.7C34 6.1 29.3 4 24 4 16.3 4 9.7 8.3 6.3 14.7z" />
      <path fill="#4CAF50" d="M24 44c5.2 0 9.9-2 13.4-5.2l-6.2-5.2C29.2 35 26.7 36 24 36c-5.4 0-9.9-3.4-11.3-8.1l-6.5 5C9.6 39.6 16.2 44 24 44z" />
      <path fill="#1976D2" d="M43.6 20.5H42V20H24v8h11.3c-.8 2.2-2.2 4.1-4.1 5.5l6.2 5.2C41.8 35.8 44 30.3 44 24c0-1.2-.1-2.3-.4-3.5z" />
    </svg>
  )
}

function AuthChip() {
  const { status, user, signInWithGoogle, signOut } = useAuth()
  const [hover, setHover] = useState(false)

  if (status === 'loading') {
    return (
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: '8px', padding: '6px 12px', fontFamily: 'var(--font-mono)', fontSize: '11px', color: 'rgba(255,255,255,0.25)', letterSpacing: '0.06em' }}>
        <span style={{ width: 6, height: 6, borderRadius: '50%', background: 'rgba(255,255,255,0.3)', animation: 'pulse 1.4s ease-in-out infinite' }} />
        Loading
      </span>
    )
  }

  if (status === 'anon') {
    return (
      <button onClick={signInWithGoogle}
        onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}
        style={{ display: 'inline-flex', alignItems: 'center', gap: '8px', padding: '7px 14px', background: 'transparent', color: hover ? '#fff' : 'rgba(255,255,255,0.55)', border: '1px solid ' + (hover ? 'rgba(255,255,255,0.28)' : 'rgba(255,255,255,0.12)'), borderRadius: '9999px', fontFamily: 'var(--font-mono)', fontSize: '11px', letterSpacing: '0.06em', cursor: 'pointer', transition: 'all 0.2s' }}>
        <GoogleGlyph />
        Sign in
      </button>
    )
  }

  const name = (user?.user_metadata?.full_name as string | undefined)?.split(' ')[0]
    || user?.email?.split('@')[0]
    || 'you'
  const truncated = name.length > 14 ? name.slice(0, 14) + '…' : name

  return (
    <button onClick={signOut}
      onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}
      title="Sign out"
      style={{ display: 'inline-flex', alignItems: 'center', gap: '8px', padding: '6px 12px 6px 6px', background: 'rgba(255,255,255,0.06)', color: hover ? '#fff' : 'rgba(255,255,255,0.75)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: '9999px', fontFamily: 'var(--font-mono)', fontSize: '11px', letterSpacing: '0.06em', cursor: 'pointer', transition: 'all 0.2s' }}>
      <span style={{ width: 20, height: 20, borderRadius: '50%', background: 'linear-gradient(135deg, #707070, #E0E0E0)', color: '#000', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', fontSize: '10px', fontWeight: 700, textTransform: 'uppercase' }}>
        {name[0]}
      </span>
      {hover ? 'Sign out' : truncated}
    </button>
  )
}

// ── hero ─────────────────────────────────────────────────────────────

function Hero({ onStudio }: { onStudio: () => void }) {
  const ref = useRef<HTMLElement>(null)
  const { scrollYProgress } = useScroll({ target: ref, offset: ['start start', 'end start'] })
  const opacity = useTransform(scrollYProgress, [0, 0.5], [1, 0])
  const y = useTransform(scrollYProgress, [0, 0.5], [0, -100])

  return (
    <section ref={ref} style={{ position: 'relative', minHeight: '100vh', display: 'flex', flexDirection: 'column', justifyContent: 'center', padding: '0 64px', overflow: 'hidden', willChange: 'transform' }}>
      {/* scattered preview images — controlled by GSAP intro timeline */}
      <img data-intro="hero-images" src="/frames/frame_045.jpg" alt=""
        style={{ position: 'absolute', top: '12%', right: '8%', width: '200px', height: '130px', objectFit: 'cover', transform: 'rotate(2deg)', border: '1px solid rgba(255,255,255,0.06)', pointerEvents: 'none', zIndex: 0, opacity: 0 }} />
      <img data-intro="hero-images" src="/frames/frame_080.jpg" alt=""
        style={{ position: 'absolute', top: '58%', left: '3%', width: '160px', height: '100px', objectFit: 'cover', transform: 'rotate(-3deg)', border: '1px solid rgba(255,255,255,0.06)', pointerEvents: 'none', zIndex: 0, opacity: 0 }} />
      <img data-intro="hero-images" src="/frames/frame_120.jpg" alt=""
        style={{ position: 'absolute', top: '40%', right: '4%', width: '180px', height: '120px', objectFit: 'cover', transform: 'rotate(1deg)', border: '1px solid rgba(255,255,255,0.06)', pointerEvents: 'none', zIndex: 0, opacity: 0 }} />

      <div
        style={{
          position: 'absolute',
          top: '50%',
          right: 'clamp(124px, 15vw, 236px)',
          transform: 'translateY(-50%)',
          height: 'clamp(344px, 50vh, 452px)',
          width: '104px',
          overflow: 'hidden',
          pointerEvents: 'none',
          zIndex: 1,
          opacity: 0.88,
        }}
      >
        <HeroLogoLoop />
      </div>

      <motion.div style={{ opacity, y }}>
        {/* top bar — chrome, hidden initially */}
        <div data-intro="chrome"
          style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end', marginBottom: '40px', opacity: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: '12px', color: 'rgba(255,255,255,0.3)', letterSpacing: '0.08em' }}>IRIS®</span>
              <div style={{ display: 'flex', gap: '16px', marginTop: '8px' }}>
                {['Localized edits', 'Continuity', 'CLI + agents'].map((tag, i) => (
                  <span key={i} style={{ fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'rgba(255,255,255,0.12)' }}>{tag}</span>
                ))}
              </div>
            </div>
            <div data-intro="chrome" style={{ fontFamily: 'var(--font-mono)', fontSize: '11px', color: 'rgba(255,255,255,0.2)', textAlign: 'right', lineHeight: 1.6, opacity: 0 }}>
              Prompt-driven video editor<br />Localized edits with continuity-aware follow-through.
            </div>
        </div>

        {/* title — centered on screen during intro, settles into layout after */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'flex-start', width: '100%', marginBottom: '48px', paddingLeft: 'clamp(112px, 14vw, 280px)' }}>
          {/* metallic paint logo — appears after title reveal */}
          <motion.div
            data-intro="metallic"
            style={{
              width: 'clamp(80px, 12vw, 200px)',
              height: 'clamp(80px, 12vw, 200px)',
              flexShrink: 0,
              opacity: 0,
              marginRight: 'clamp(-18px, -1.4vw, -10px)',
              willChange: 'transform',
              backfaceVisibility: 'hidden',
              zIndex: 2,
            }}
          >
            <MetallicPaint imageSrc={IRIS_SVG} {...FLOWER_METALLIC_PROPS} />
          </motion.div>

          <div data-intro="logo-dark"
            style={{ opacity: 0, display: 'flex', flexDirection: 'column', alignItems: 'flex-start', gap: '18px', filter: 'drop-shadow(0 0 60px rgba(255,255,255,0.08))' }}>
            <div style={{ position: 'relative', width: 'clamp(560px, 62vw, 1240px)', height: 'clamp(128px, 15vw, 252px)', marginLeft: 'clamp(-260px, -15vw, -160px)', marginTop: 'clamp(14px, 1.4vw, 24px)' }}>
              <MetallicPaint text={IRIS_WORDMARK_TEXT} textOptions={IRIS_WORDMARK_MASK} {...WORDMARK_METALLIC_PROPS} />
              <span style={{ position: 'absolute', width: 1, height: 1, padding: 0, margin: -1, overflow: 'hidden', clip: 'rect(0, 0, 0, 0)', whiteSpace: 'nowrap', border: 0 }}>
                {IRIS_WORDMARK_TEXT}
              </span>
            </div>

            <div style={{ marginLeft: 'clamp(36px, 4.2vw, 74px)', display: 'flex', alignItems: 'center', gap: '14px' }}>
              <Magnetic intensity={0.18}>
                <button onClick={onStudio} style={{ fontFamily: 'var(--font-body)', fontSize: '15px', padding: '15px 34px', border: '1px solid rgba(255,255,255,0.58)', width: '194px', background: 'linear-gradient(135deg, rgba(248,250,252,0.98) 0%, rgba(226,232,240,0.96) 52%, rgba(255,255,255,0.98) 100%)', backgroundSize: '200% 100%', color: '#000', fontWeight: 400, letterSpacing: '0.04em', cursor: 'pointer', animation: 'shimmer 6s ease-in-out infinite', transition: 'box-shadow 0.3s, transform 0.3s', textAlign: 'center', boxSizing: 'border-box', textTransform: 'none', lineHeight: 1.1, boxShadow: '0 0 0 1px rgba(255,255,255,0.24) inset, 0 18px 48px rgba(186,220,255,0.18)' }}
                  onMouseEnter={e => (e.currentTarget.style.boxShadow = '0 0 0 1px rgba(255,255,255,0.3) inset, 0 22px 56px rgba(190,220,255,0.26)')} onMouseLeave={e => (e.currentTarget.style.boxShadow = '0 0 0 1px rgba(255,255,255,0.24) inset, 0 18px 48px rgba(186,220,255,0.18)')}>Open Studio</button>
              </Magnetic>
              <Magnetic intensity={0.18}>
                <button onClick={() => window.open('https://docs.useiris.tech/product/editor-workflow', '_blank', 'noopener,noreferrer')} style={{ fontFamily: 'var(--font-body)', fontSize: '15px', padding: '15px 34px', border: '1px solid rgba(186,220,255,0.2)', width: '194px', background: 'linear-gradient(135deg, rgba(16,18,22,0.96), rgba(4,4,6,0.94), rgba(18,22,28,0.96), rgba(4,4,6,0.94), rgba(16,18,22,0.96))', backgroundSize: '200% 100%', color: 'rgba(236,244,255,0.92)', fontWeight: 400, letterSpacing: '0.04em', cursor: 'pointer', animation: 'shimmer 6s ease-in-out infinite', transition: 'box-shadow 0.3s, transform 0.3s', textAlign: 'center', boxSizing: 'border-box', textTransform: 'none', lineHeight: 1.1, boxShadow: '0 0 0 1px rgba(255,255,255,0.05) inset, 0 16px 42px rgba(118,150,188,0.12)' }}
                  onMouseEnter={e => (e.currentTarget.style.boxShadow = '0 0 0 1px rgba(255,255,255,0.08) inset, 0 20px 50px rgba(190,220,255,0.18)')} onMouseLeave={e => (e.currentTarget.style.boxShadow = '0 0 0 1px rgba(255,255,255,0.05) inset, 0 16px 42px rgba(118,150,188,0.12)')}>View Workflow</button>
              </Magnetic>
            </div>
          </div>
        </div>

        {/* bottom row */}
        <div data-intro="chrome"
          style={{ display: 'grid', gridTemplateColumns: '1fr auto', gap: '40px', alignItems: 'end', opacity: 0 }}>
          <div style={{ maxWidth: '320px' }}>
            <p data-intro="subtext" style={{ fontFamily: 'var(--font-body)', fontSize: '14px', lineHeight: 1.7, color: 'rgba(255,255,255,0.4)', opacity: 0, marginBottom: '6px' }}>
              Select the moment.
            </p>
            <p data-intro="subtext" style={{ fontFamily: 'var(--font-body)', fontSize: '14px', lineHeight: 1.7, color: 'rgba(255,255,255,0.4)', opacity: 0, marginBottom: '6px' }}>
              Describe the change.
            </p>
            <p data-intro="subtext" style={{ fontFamily: 'var(--font-body)', fontSize: '14px', lineHeight: 1.7, color: 'rgba(255,255,255,0.4)', opacity: 0 }}>
              Accept the strongest take.
            </p>
          </div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: '11px', color: 'rgba(255,255,255,0.15)', lineHeight: 2, textAlign: 'right' }}>
            <div>Localized video editing</div><div>Variant review + acceptance</div><div>Continuity-aware workflow</div>
          </div>
        </div>
      </motion.div>

      <div data-intro="chrome" style={{ position: 'absolute', bottom: '32px', left: '50%', transform: 'translateX(-50%)', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '8px', opacity: 0 }}>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'rgba(255,255,255,0.12)', letterSpacing: '0.16em' }}>Scroll</span>
        <motion.div style={{ width: '1px', height: '24px', background: 'rgba(255,255,255,0.12)' }} animate={{ scaleY: [1, 0.4, 1] }} transition={{ duration: 2.5, repeat: Infinity, ease: 'easeInOut' }} />
      </div>
    </section>
  )
}

// ── scroll reveal ──────────────────────────────────────────────────
// scroll-linked opacity+y, replaces whileInView to eliminate lenis/IO flicker

function ScrollReveal({ children, y: yOffset = 30, className, style, as: Tag = 'div', delay = 0 }: {
  children: React.ReactNode
  y?: number
  className?: string
  style?: React.CSSProperties
  as?: 'div' | 'section' | 'p' | 'h2'
  delay?: number
}) {
  const ref = useRef<HTMLDivElement>(null)
  const { scrollYProgress } = useScroll({
    target: ref,
    offset: ['start end', 'end 80%'],  // animate as element crosses bottom 20% of viewport
  })

  // map scroll progress to opacity and y — with optional delay (shifts the active range)
  const clampedProgress = useTransform(scrollYProgress, [delay, Math.min(delay + 0.6, 1)], [0, 1], { clamp: true })
  const opacity = useTransform(clampedProgress, [0, 1], [0, 1])
  const yVal = useTransform(clampedProgress, [0, 1], [yOffset, 0])

  const MotionTag = motion[Tag] as typeof motion.div

  return (
    <MotionTag ref={ref} className={className} style={{ ...style, opacity, y: yVal }}>
      {children}
    </MotionTag>
  )
}

// ── logo loop ───────────────────────────────────────────────────────

const HERO_LOGOS: LogoLoopItem[] = [
  { src: 'https://cdn.simpleicons.org/react/61DAFB', alt: 'React', title: 'React' },
  { src: 'https://cdn.simpleicons.org/vite/646CFF', alt: 'Vite', title: 'Vite' },
  { src: 'https://cdn.simpleicons.org/typescript/3178C6', alt: 'TypeScript', title: 'TypeScript' },
  { src: 'https://cdn.simpleicons.org/framer/FFFFFF', alt: 'Motion', title: 'Motion' },
  { src: 'https://cdn.simpleicons.org/greensock/88CE02', alt: 'GSAP', title: 'GSAP' },
  { src: 'https://cdn.simpleicons.org/supabase/3ECF8E', alt: 'Supabase', title: 'Supabase' },
  { src: 'https://cdn.simpleicons.org/fastapi/009688', alt: 'FastAPI', title: 'FastAPI' },
  { src: 'https://cdn.simpleicons.org/python/3776AB', alt: 'Python', title: 'Python' },
  { src: 'https://cdn.simpleicons.org/alibabacloud/FF6A00', alt: 'Qwen', title: 'Qwen' },
  { src: 'https://cdn.simpleicons.org/postgresql/4169E1', alt: 'Postgres', title: 'Postgres' },
  { src: 'https://cdn.simpleicons.org/vultr/007BFC', alt: 'Vultr', title: 'Vultr' },
]

function HeroLogoLoop() {
  return (
    <LogoLoop
      logos={HERO_LOGOS}
      speed={34}
      direction="up"
      width="104px"
      logoHeight={48}
      gap={46}
      hoverSpeed={12}
      fadeOut
      ariaLabel="Iris tech stack loop"
      style={{
        height: '100%',
      }}
      renderItem={(item, key) => {
        if (!('src' in item)) return null
        return (
          <img
            key={key}
            src={item.src}
            alt={item.alt ?? 'Technology logo'}
            title={item.title}
            style={{
              height: '48px',
              width: '48px',
              objectFit: 'contain',
              opacity: 0.94,
              filter: 'grayscale(1) brightness(2.15) contrast(0.82) drop-shadow(0 0 10px rgba(236,240,246,0.5)) drop-shadow(0 0 22px rgba(226,232,240,0.32)) drop-shadow(0 0 40px rgba(214,220,230,0.18)) drop-shadow(0 0 64px rgba(214,220,230,0.1))',
              background: 'transparent',
              pointerEvents: 'none',
              userSelect: 'none',
            }}
          />
        )
      }}
    />
  )
}

// ── thesis ──────────────────────────────────────────────────────────

function SectionDivider() {
  return (
    <div
      aria-hidden="true"
      style={{
        position: 'relative',
        width: '100%',
        padding: '0 64px',
        display: 'flex',
        justifyContent: 'center',
        pointerEvents: 'none',
      }}
    >
      <div
        style={{
          position: 'relative',
          width: 'min(1200px, 100%)',
          height: '1px',
          background:
            'linear-gradient(90deg, rgba(255,255,255,0) 0%, rgba(163,172,184,0.42) 18%, rgba(236,240,246,0.94) 50%, rgba(163,172,184,0.42) 82%, rgba(255,255,255,0) 100%)',
          boxShadow: '0 0 18px rgba(224,232,240,0.12), 0 0 42px rgba(255,255,255,0.06)',
        }}
      >
        <div
          style={{
            position: 'absolute',
            inset: 0,
            background:
              'linear-gradient(90deg, rgba(255,255,255,0) 0%, rgba(255,255,255,0.16) 35%, rgba(255,255,255,0.38) 50%, rgba(255,255,255,0.16) 65%, rgba(255,255,255,0) 100%)',
            mixBlendMode: 'screen',
          }}
        />
      </div>
    </div>
  )
}

function SectionStage({
  children,
  id,
  as = 'section',
  style,
}: {
  children: React.ReactNode
  id?: string
  as?: 'section' | 'footer' | 'div'
  style?: React.CSSProperties
}) {
  const targetRef = useRef<HTMLDivElement>(null)
  const { scrollYProgress } = useScroll({
    target: targetRef,
    offset: ['start 88%', 'end 24%'],
  })

  const opacity = useTransform(scrollYProgress, [0, 0.22, 1], [0.24, 1, 1])
  const y = useTransform(scrollYProgress, [0, 0.32], [64, 0])
  const scale = useTransform(scrollYProgress, [0, 0.32], [0.985, 1])
  const beamScale = useTransform(scrollYProgress, [0, 0.28], [0.35, 1])
  const beamOpacity = useTransform(scrollYProgress, [0, 0.18, 0.55], [0, 0.95, 0.22])

  const MotionTag =
    as === 'footer'
      ? motion.footer
      : as === 'div'
        ? motion.div
        : motion.section

  return (
    <MotionTag
      id={id}
      style={{
        position: 'relative',
        transformOrigin: '50% 18%',
        opacity,
        y,
        scale,
        ...style,
      }}
    >
      <div
        ref={targetRef}
        aria-hidden="true"
        style={{ position: 'absolute', inset: 0, pointerEvents: 'none' }}
      />
      <motion.div
        aria-hidden="true"
        style={{
          position: 'absolute',
          top: 0,
          left: '50%',
          width: 'min(720px, 72vw)',
          height: '1px',
          transform: 'translateX(-50%)',
          transformOrigin: '50% 50%',
          scaleX: beamScale,
          opacity: beamOpacity,
          background:
            'linear-gradient(90deg, rgba(255,255,255,0) 0%, rgba(178,188,200,0.24) 22%, rgba(242,246,252,0.92) 50%, rgba(178,188,200,0.24) 78%, rgba(255,255,255,0) 100%)',
          boxShadow: '0 0 18px rgba(236,242,248,0.12), 0 0 38px rgba(255,255,255,0.05)',
          pointerEvents: 'none',
        }}
      />
      <motion.div
        aria-hidden="true"
        style={{
          position: 'absolute',
          top: '-80px',
          left: '50%',
          width: 'min(860px, 82vw)',
          height: '180px',
          transform: 'translateX(-50%)',
          opacity: useTransform(scrollYProgress, [0, 0.25, 0.6], [0, 0.16, 0]),
          background:
            'radial-gradient(circle at 50% 0%, rgba(214,224,236,0.16) 0%, rgba(214,224,236,0.06) 28%, rgba(214,224,236,0) 70%)',
          filter: 'blur(18px)',
          pointerEvents: 'none',
        }}
      />
      {children}
    </MotionTag>
  )
}

const STEPS = [
  { num: '01', label: 'scrub', desc: 'Scrub to the exact frame or range you want to change.' },
  { num: '02', label: 'select', desc: 'Draw a box or use a mask-assisted selection to isolate the subject or region.' },
  { num: '03', label: 'prompt', desc: 'Describe the change in plain language and let Iris generate candidate edits.' },
  { num: '04', label: 'accept', desc: 'Compare the variants, accept the strongest result, and carry it forward through the cut.' },
]

function Thesis() {
  const [activeStep, setActiveStep] = useState(0)

  useEffect(() => {
    const intervalId = window.setInterval(() => {
      setActiveStep((current) => (current + 1) % STEPS.length)
    }, 2400)

    return () => window.clearInterval(intervalId)
  }, [])
  return (
    <SectionStage id="about" style={{ padding: '200px 64px 160px', maxWidth: '1200px', margin: '0 auto' }}>
      {/* decorative section number */}
      <div style={{ position: 'absolute', top: '80px', right: '0', fontFamily: 'var(--font-display)', fontSize: '300px', fontWeight: 300, color: '#fff', opacity: 0.03, lineHeight: 1, pointerEvents: 'none', userSelect: 'none' }}>01</div>

      {/* scattered images — low target opacity, so just use inline style fade */}
      <img src="/frames/frame_040.jpg" alt=""
        style={{ position: 'absolute', top: '10%', right: '6%', width: '240px', height: '160px', objectFit: 'cover', rotate: '-1deg', border: '1px solid rgba(255,255,255,0.06)', pointerEvents: 'none', zIndex: 1, opacity: 0.25 }} />
      <img src="/frames/frame_110.jpg" alt=""
        style={{ position: 'absolute', bottom: '12%', left: '2%', width: '200px', height: '140px', objectFit: 'cover', rotate: '2deg', border: '1px solid rgba(255,255,255,0.06)', pointerEvents: 'none', zIndex: 1, opacity: 0.2 }} />

      <ScrollReveal y={0}
        style={{ fontFamily: 'var(--font-mono)', fontSize: '11px', color: 'rgba(255,255,255,0.2)', letterSpacing: '0.2em', marginBottom: '40px' }}>© iris — 001 / about</ScrollReveal>

      <ScrollReveal as="h2" y={-40}
        style={{ fontFamily: 'var(--font-display)', fontWeight: 300, fontSize: 'clamp(48px, 8vw, 128px)', lineHeight: 0.95, letterSpacing: '-0.03em', color: '#fff', marginBottom: '0' }}>
            change one moment.<br /><span style={{ background: 'linear-gradient(135deg, #707070, #B0B0B0, #E0E0E0, #B0B0B0, #707070)', backgroundSize: '200% 100%', WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent', animation: 'shimmer 8s ease-in-out infinite' }}>keep the cut.</span>
      </ScrollReveal>

      {/* divider line */}
      <div style={{ width: '100%', height: '1px', background: 'rgba(255,255,255,0.06)', margin: '40px 0' }} />

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '120px' }}>
        <div>
          <ScrollReveal as="p"
            style={{ fontFamily: 'var(--font-body)', fontSize: '18px', lineHeight: 1.78, color: 'rgba(255,255,255,0.5)', marginBottom: '32px', maxWidth: '34ch' }}>
            Iris is built for specific editorial changes, not full-scene regeneration. Scrub to an exact frame or short range, isolate the subject, and tell Iris what should change.
          </ScrollReveal>
          <ScrollReveal as="p" delay={0.1}
            style={{ fontFamily: 'var(--font-body)', fontSize: '18px', lineHeight: 1.78, color: 'rgba(255,255,255,0.44)', marginBottom: '48px', maxWidth: '36ch' }}>
            Instead of locking you into one opaque output, Iris generates multiple interpretations for the moment you selected. You review the options, accept the strongest take, and continue with a timeline that still feels authored.
          </ScrollReveal>
          <ScrollReveal y={0} delay={0.2}
            style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '24px', borderTop: '1px solid rgba(255,255,255,0.06)', paddingTop: '24px' }}>
            {[{ val: '1 moment', label: 'selected with precision' }, { val: 'N variants', label: 'reviewed before acceptance' }, { val: '1 timeline', label: 'updated with continuity' }].map((s, i) => (
              <div key={i} style={{ cursor: 'default', transition: 'transform 0.3s, text-shadow 0.3s' }}
                onMouseEnter={e => { e.currentTarget.style.transform = 'translateY(-4px)'; const valEl = e.currentTarget.querySelector('[data-stat-val]') as HTMLElement | null; if (valEl) { valEl.style.color = '#fff'; valEl.style.textShadow = '0 0 40px rgba(255,255,255,0.15)' } }}
                onMouseLeave={e => { e.currentTarget.style.transform = 'translateY(0)'; const valEl = e.currentTarget.querySelector('[data-stat-val]') as HTMLElement | null; if (valEl) { valEl.style.color = '#fff'; valEl.style.textShadow = 'none' } }}>
                <div data-stat-val="" style={{ fontFamily: 'var(--font-display)', fontSize: '32px', fontWeight: 300, color: '#fff', letterSpacing: '-0.02em', marginBottom: '4px', transition: 'color 0.3s, text-shadow 0.3s' }}>{s.val}</div>
                <div style={{ fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'rgba(255,255,255,0.2)', letterSpacing: '0.1em' }}>{s.label}</div>
              </div>
            ))}
          </ScrollReveal>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column' }}>
          {STEPS.map((s, i) => {
            const isActive = activeStep === i
            return (
              <ScrollReveal key={s.num} delay={i * 0.08}
                style={{
                  padding: isActive ? '28px 18px' : '28px 0',
                  cursor: 'default',
                  borderBottom: '1px solid ' + (isActive ? 'rgba(255,255,255,0.16)' : 'rgba(255,255,255,0.06)'),
                  borderRadius: '18px',
                  background: isActive ? 'linear-gradient(135deg, rgba(255,255,255,0.05), rgba(186,220,255,0.035), rgba(255,255,255,0.015))' : 'transparent',
                  boxShadow: isActive ? '0 0 0 1px rgba(255,255,255,0.04) inset, 0 18px 44px rgba(186,220,255,0.08)' : 'none',
                  transition: 'all 0.45s ease',
                }}>
                <div onMouseEnter={() => setActiveStep(i)} style={{ display: 'flex', alignItems: 'baseline', gap: '20px', marginBottom: '8px' }}>
                  <span data-step-num="" style={{ fontFamily: 'var(--font-mono)', fontSize: '10px', color: isActive ? 'rgba(255,255,255,0.62)' : 'rgba(255,255,255,0.15)', letterSpacing: '0.15em', minWidth: '20px', transition: 'color 0.3s' }}>{s.num}</span>
                  <span style={{ fontFamily: 'var(--font-display)', fontSize: '26px', fontWeight: 300, color: isActive ? '#fff' : 'rgba(255,255,255,0.82)', letterSpacing: '-0.02em', transition: 'color 0.3s' }}>{s.label}</span>
                </div>
                <div style={{ fontFamily: 'var(--font-body)', fontSize: '16px', color: isActive ? 'rgba(255,255,255,0.68)' : 'rgba(255,255,255,0.34)', lineHeight: 1.72, paddingLeft: '40px', transition: 'color 0.3s' }}>{s.desc}</div>
              </ScrollReveal>
            )
          })}
        </div>
      </div>
    </SectionStage>
  )
}

// ── features ────────────────────────────────────────────────────────

function Features() {
  return (
    <SectionStage id="features" style={{ padding: '160px 64px', maxWidth: '1200px', margin: '0 auto' }}>
      <ScrollReveal y={0}
        style={{ fontFamily: 'var(--font-mono)', fontSize: '11px', color: 'rgba(255,255,255,0.2)', letterSpacing: '0.2em', marginBottom: '40px' }}>© iris — 002 / capabilities</ScrollReveal>

      <ScrollReveal as="h2" y={-40}
        style={{ fontFamily: 'var(--font-display)', fontWeight: 300, fontSize: 'clamp(40px, 6vw, 96px)', lineHeight: 0.95, letterSpacing: '-0.03em', color: '#fff', marginBottom: '80px' }}>
        from prompt<br /><span style={{ color: 'rgba(255,255,255,0.4)' }}>to continuity.</span>
      </ScrollReveal>

      <ScrollReveal y={24}>
        <CardSwapShowcase />
      </ScrollReveal>
    </SectionStage>
  )
}

// ── social proof ───────────────────────────────────────────────────

const INSTALL_CMD = `Install the iris video editing CLI and agent skill file. Run these commands:
1. pip install iris-edit
2. mkdir -p ~/.claude/skills/iris-edit && curl -sL https://raw.githubusercontent.com/stephenhungg/iris/main/cli/SKILL.md -o ~/.claude/skills/iris-edit/SKILL.md
3. iris auth login --base-url https://useiris.tech
Then say "iris edit" to start editing videos.`

function CopyInstallButton() {
  const [copied, setCopied] = useState(false)
  return (
    <button
      onClick={() => {
        navigator.clipboard.writeText(INSTALL_CMD).then(() => {
          setCopied(true)
          setTimeout(() => setCopied(false), 2000)
        })
      }}
      style={{
        marginTop: '6px',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        gap: '8px',
        width: '100%',
        padding: '10px 0',
        border: '1px solid rgba(255,255,255,0.08)',
        borderRadius: '8px',
        background: copied ? 'rgba(126,231,135,0.08)' : 'rgba(255,255,255,0.03)',
        color: copied ? 'rgba(126,231,135,0.9)' : 'rgba(255,255,255,0.5)',
        fontFamily: 'var(--font-mono)',
        fontSize: '10px',
        letterSpacing: '0.12em',
        cursor: 'pointer',
        transition: 'all 0.2s ease',
      }}
    >
      {copied ? 'copied to clipboard' : 'copy agent install prompt'}
    </button>
  )
}

const AGENT_TERMINAL_LINES = [
  '$ pip install iris-edit',
  '$ mkdir -p ~/.claude/skills/iris-edit && curl -sL https://raw.githubusercontent.com/stephenhungg/iris/main/cli/SKILL.md -o ~/.claude/skills/iris-edit/SKILL.md',
  '$ iris analyze proj_iris --fps 2.0',
  '$ iris generate --project proj_iris --start 12.0 --end 14.0 --bbox "0.2,0.3,0.4,0.3" --prompt "replace the phone with a chrome orb"',
  '$ iris score --compare var_01 var_02 var_03',
  '$ iris accept --job job_abc --variant 0',
  '$ iris export proj_iris',
]

function SocialProof() {
  return (
    <SectionStage id="agents" style={{ padding: '160px 64px', maxWidth: '1200px', margin: '0 auto' }}>
      <ScrollReveal y={0}
        style={{ fontFamily: 'var(--font-mono)', fontSize: '11px', color: 'rgba(255,255,255,0.2)', letterSpacing: '0.2em', marginBottom: '64px' }}>© iris — 003 / signal</ScrollReveal>

      <ScrollReveal as="h2" y={-24}
        style={{ fontFamily: 'var(--font-display)', fontWeight: 300, fontSize: 'clamp(36px, 5vw, 80px)', lineHeight: 0.95, letterSpacing: '-0.03em', color: '#fff', marginBottom: '24px' }}>
        iris works<br />in the terminal.
      </ScrollReveal>

      <ScrollReveal as="p" y={0}
        style={{ maxWidth: '760px', fontFamily: 'var(--font-mono)', fontSize: '12px', color: 'rgba(255,255,255,0.28)', letterSpacing: '0.08em', lineHeight: 1.9, marginBottom: '56px' }}>
        The Iris CLI wraps the backend for scripting, automation, and agent-driven editing. Any agent that can run shell commands can inspect projects, trigger localized changes, review variants, and export results without a custom integration.
      </ScrollReveal>

      <ScrollReveal as="p" y={0} delay={0.06}
        style={{ maxWidth: '760px', fontFamily: 'var(--font-body)', fontSize: '16px', color: 'rgba(255,255,255,0.42)', lineHeight: 1.8, marginBottom: '56px' }}>
        Read the full workflow in the{' '}
        <a
          href="https://docs.useiris.tech"
          target="_blank"
          rel="noreferrer"
          style={{
            color: 'rgba(236,244,255,0.9)',
            textDecoration: 'none',
            borderBottom: '1px solid rgba(186,220,255,0.28)',
            paddingBottom: '2px',
          }}
        >
          docs
        </a>
        {' '}for setup, project structure, continuity, and CLI usage.
      </ScrollReveal>

      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1.15fr) minmax(360px, 0.85fr)', gap: '40px', alignItems: 'stretch' }}>
        <ScrollReveal
          style={{ borderTop: '1px solid rgba(255,255,255,0.06)', borderBottom: '1px solid rgba(255,255,255,0.06)', padding: '44px 0' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: '28px' }}>
            <div style={{ fontFamily: 'var(--font-display)', fontSize: '30px', fontWeight: 300, color: '#fff', letterSpacing: '-0.025em' }}>
              the same workflow, scriptable.
            </div>
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'rgba(255,255,255,0.08)', letterSpacing: '0.15em' }}>CLI-01</span>
          </div>

          <p style={{ fontFamily: 'var(--font-display)', fontSize: '22px', lineHeight: 1.5, color: 'rgba(255,255,255,0.5)', marginBottom: '28px', maxWidth: '720px' }}>
            The CLI mirrors the core product loop: preview footage, generate changes, score variants, accept the best result, and export when the cut is ready. A portable skill file gives agents the setup context they need without custom integrations.
          </p>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '0', borderTop: '1px solid rgba(255,255,255,0.06)' }}>
            {[
              { label: 'surface', value: 'CLI + API', note: 'One workflow across the app and terminal' },
              { label: 'coverage', value: 'Projects to export', note: 'Preview, generate, score, accept, and export' },
              { label: 'automation', value: 'Agent-friendly', note: 'Shell-first and scriptable by design' },
            ].map((item, i) => (
              <div key={item.label} style={{ padding: '24px 24px 0 0', paddingLeft: i > 0 ? '24px' : '0', borderRight: i < 2 ? '1px solid rgba(255,255,255,0.06)' : 'none' }}>
                <div style={{ fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'rgba(255,255,255,0.16)', letterSpacing: '0.14em', marginBottom: '12px' }}>{item.label}</div>
                <div style={{ fontFamily: 'var(--font-display)', fontSize: '24px', fontWeight: 300, color: '#fff', letterSpacing: '-0.02em', marginBottom: '10px' }}>{item.value}</div>
                <div style={{ fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'rgba(255,255,255,0.22)', letterSpacing: '0.1em', lineHeight: 1.7 }}>{item.note}</div>
              </div>
            ))}
          </div>
        </ScrollReveal>

        <ScrollReveal delay={0.08}>
          <div style={{ position: 'relative', height: '100%', minHeight: '420px', border: '1px solid rgba(255,255,255,0.08)', background: 'linear-gradient(180deg, rgba(255,255,255,0.045) 0%, rgba(255,255,255,0.02) 100%)', boxShadow: '0 32px 100px rgba(0,0,0,0.34), inset 0 1px 0 rgba(255,255,255,0.04)', backdropFilter: 'blur(18px)', overflow: 'hidden' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '14px 18px', borderBottom: '1px solid rgba(255,255,255,0.06)', background: 'rgba(255,255,255,0.02)' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                {['rgba(255,255,255,0.18)', 'rgba(255,255,255,0.14)', 'rgba(255,255,255,0.1)'].map(dot => (
                  <span key={dot} style={{ width: '8px', height: '8px', borderRadius: '50%', background: dot, display: 'block' }} />
                ))}
              </div>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'rgba(255,255,255,0.18)', letterSpacing: '0.14em' }}>agent bootstrap / terminal</div>
            </div>

            <div style={{ padding: '22px 20px 26px', display: 'flex', flexDirection: 'column', gap: '14px', fontFamily: 'var(--font-mono)', fontSize: '11px', color: 'rgba(255,255,255,0.78)', lineHeight: 1.7 }}>
              {AGENT_TERMINAL_LINES.map((line, i) => (
                <div key={line} style={{ opacity: 1 - i * 0.02 }}>
                  <span style={{ color: 'rgba(186,220,255,0.85)' }}>{'>'}</span> {line}
                </div>
              ))}

              <div style={{ marginTop: '8px', paddingTop: '14px', borderTop: '1px solid rgba(255,255,255,0.06)', color: 'rgba(255,255,255,0.32)', letterSpacing: '0.08em' }}>
                one file. any agent can edit video.
              </div>

              <CopyInstallButton />
            </div>
          </div>
        </ScrollReveal>
      </div>
    </SectionStage>
  )
}

// ── tech strip ──────────────────────────────────────────────────────

const TECH = [
  { name: 'localized edits', role: 'point at the exact moment', code: '01' },
  { name: 'variants', role: 'compare multiple takes', code: '02' },
  { name: 'continuity', role: 'carry edits through the shot', code: '03' },
  { name: 'projects + library', role: 'organize media and timelines', code: '04' },
  { name: 'cli + api', role: 'script and automate the workflow', code: '05' },
]

function TechStrip() {
  return (
    <SectionStage
      as="section"
      style={{ padding: '80px 64px', borderTop: '1px solid rgba(255,255,255,0.06)', borderBottom: '1px solid rgba(255,255,255,0.06)', display: 'flex', justifyContent: 'center', gap: '0' }}
    >
      {TECH.map((t, i) => (
        <div key={i} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '8px', padding: '0 40px', borderRight: i < TECH.length - 1 ? '1px solid rgba(255,255,255,0.06)' : 'none', cursor: 'default', transition: 'all 0.3s' }}
          onMouseEnter={e => { const name = e.currentTarget.querySelector('[data-tech-name]') as HTMLElement | null; if (name) name.style.color = 'rgba(255,255,255,0.7)' }}
          onMouseLeave={e => { const name = e.currentTarget.querySelector('[data-tech-name]') as HTMLElement | null; if (name) name.style.color = 'rgba(255,255,255,0.3)' }}>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'rgba(255,255,255,0.08)', letterSpacing: '0.15em' }}>{t.code}</span>
          <span data-tech-name="" style={{ fontFamily: 'var(--font-mono)', fontSize: '12px', color: 'rgba(255,255,255,0.3)', letterSpacing: '0.1em', transition: 'color 0.3s' }}>{t.name}</span>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'rgba(255,255,255,0.15)', letterSpacing: '0.15em' }}>{t.role}</span>
        </div>
      ))}
    </SectionStage>
  )
}

// ── cta ─────────────────────────────────────────────────────────────

function CTA({ onStudio }: { onStudio: () => void }) {
  return (
    <SectionStage id="rewrite" style={{ padding: '200px 64px', textAlign: 'center', overflow: 'hidden', isolation: 'isolate' }}>
      <SmokeOrbBackground />
      <div style={{ position: 'absolute', inset: 0, zIndex: 0, pointerEvents: 'none', background: 'linear-gradient(to bottom, rgba(0,0,0,0.4) 0%, rgba(0,0,0,0.12) 18%, rgba(0,0,0,0.02) 40%, rgba(0,0,0,0.08) 74%, rgba(0,0,0,0.36) 100%)' }} />
      <div style={{ position: 'relative', zIndex: 1 }}>
      <ScrollReveal y={0}
        style={{ fontFamily: 'var(--font-mono)', fontSize: '11px', color: 'rgba(255,255,255,0.2)', letterSpacing: '0.2em', marginBottom: '64px' }}>© iris — 004 / rewrite</ScrollReveal>

      <ScrollReveal y={0}
        style={{ display: 'flex', justifyContent: 'center', marginBottom: '64px' }}>
        <div
          style={{
            position: 'relative',
            width: 'min(1040px, 100%)',
            height: '260px',
            borderRadius: '28px',
            border: '1px solid rgba(255,255,255,0.06)',
            background: 'linear-gradient(180deg, rgba(8,8,10,0.58) 0%, rgba(10,10,12,0.42) 100%)',
            boxShadow: '0 40px 120px rgba(0,0,0,0.34), inset 0 0 0 1px rgba(255,255,255,0.02)',
            backdropFilter: 'blur(22px)',
            overflow: 'hidden',
          }}
        >
          <div
            style={{
              position: 'absolute',
              inset: 0,
              background: 'radial-gradient(circle at 50% 50%, rgba(255,255,255,0.04), transparent 62%)',
              pointerEvents: 'none',
              zIndex: 0,
            }}
          />
          <div
            style={{
              position: 'absolute',
              inset: '-18%',
              background: 'radial-gradient(circle at 50% 50%, rgba(0,0,0,0.42), transparent 68%)',
              filter: 'blur(38px)',
              pointerEvents: 'none',
              zIndex: 0,
            }}
          />
          <ASCIIText
            text="<iris>"
            enableWaves
            asciiFontSize={7}
            textFontSize={272}
            planeBaseHeight={15.5}
            textColor="#f4f4f4"
          />
        </div>
      </ScrollReveal>

      <ScrollReveal as="h2" y={-40}
        style={{ fontFamily: 'var(--font-display)', fontWeight: 300, fontSize: 'clamp(48px, 8vw, 128px)', lineHeight: 0.95, letterSpacing: '-0.03em', color: '#fff', marginBottom: '32px' }}>
        edit the part<br /><span>that matters.</span>
      </ScrollReveal>
      <ScrollReveal as="p" y={0} delay={0.1}
        style={{ fontFamily: 'var(--font-mono)', fontSize: '13px', color: 'rgba(255,255,255,0.25)', letterSpacing: '0.1em', marginBottom: '48px' }}>
        Open the studio, upload a clip, and make one localized edit. Review the options, accept the right take, and keep moving.
      </ScrollReveal>
      <ScrollReveal delay={0.2}>
        <Magnetic intensity={0.2}>
          <button onClick={onStudio}
            style={{ fontFamily: 'var(--font-body)', fontSize: '16px', padding: '18px 56px', border: '1px solid rgba(255,255,255,0.58)', background: 'linear-gradient(135deg, rgba(248,250,252,0.98) 0%, rgba(226,232,240,0.96) 52%, rgba(255,255,255,0.98) 100%)', color: '#000', fontWeight: 400, letterSpacing: '0.045em', cursor: 'pointer', transition: 'all 0.3s', textTransform: 'none', lineHeight: 1.1, boxShadow: '0 0 0 1px rgba(255,255,255,0.24) inset, 0 22px 58px rgba(186,220,255,0.18)' }}
            onMouseEnter={e => (e.currentTarget.style.boxShadow = '0 0 0 1px rgba(255,255,255,0.32) inset, 0 28px 72px rgba(190,220,255,0.26)')} onMouseLeave={e => (e.currentTarget.style.boxShadow = '0 0 0 1px rgba(255,255,255,0.24) inset, 0 22px 58px rgba(186,220,255,0.18)')}>Open Studio</button>
        </Magnetic>
      </ScrollReveal>
      <div style={{ display: 'none', fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'rgba(255,255,255,0.15)', marginTop: '20px' }}>
        no account required · free during beta
      </div>
      </div>
    </SectionStage>
  )
}

// ── footer ──────────────────────────────────────────────────────────

function Footer() {
  const footerLinks: Record<string, Array<{ label: string; href: string }>> = {
    People: [
      { label: 'Stephen', href: 'https://www.linkedin.com/in/stephen-h-hung/' },
      { label: 'Matthew', href: 'https://www.linkedin.com/in/matthew-y-kim/' },
      { label: 'Silas', href: 'https://www.linkedin.com/in/silaswu4/' },
      { label: 'Angelina', href: 'https://www.linkedin.com/in/angelina-sun-13014131b/' },
    ],
    Links: [
      { label: 'Docs', href: 'https://docs.useiris.tech' },
      { label: 'Quickstart', href: 'https://docs.useiris.tech/quickstart' },
      { label: 'Workflow', href: 'https://docs.useiris.tech/product/editor-workflow' },
      { label: 'GitHub', href: 'https://github.com/stephenhungg/iris' },
    ],
  }

  return (
    <SectionStage as="footer" style={{ padding: '120px 64px 60px', borderTop: '1px solid rgba(255,255,255,0.06)', overflow: 'hidden' }}>
      {/* ghost watermark */}
      <div style={{ position: 'absolute', top: '50%', left: '50%', transform: 'translate(-50%, -50%)', fontFamily: 'var(--font-display)', fontSize: 'clamp(120px, 20vw, 300px)', fontWeight: 300, color: '#fff', opacity: 0.03, pointerEvents: 'none', userSelect: 'none', letterSpacing: '-0.03em' }}>iris</div>

      {/* closing philosophy */}
      <ScrollReveal as="p" y={0}
          style={{ fontFamily: 'var(--font-display)', fontSize: 'clamp(20px, 3vw, 36px)', color: 'rgba(255,255,255,0.12)', lineHeight: 1.1, marginBottom: '64px' }}>
        specific edits, authored outcomes.
      </ScrollReveal>

      {/* link grid */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', maxWidth: '360px', paddingBottom: '40px', borderBottom: '1px solid rgba(255,255,255,0.06)', marginBottom: '40px' }}>
        {Object.entries(footerLinks).map(([heading, links]) => (
          <div key={heading}>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'rgba(255,255,255,0.2)', letterSpacing: '0.15em', marginBottom: '12px' }}>{heading}</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
              {links.map(link => (
                <a key={link.label} href={link.href} target={link.href.startsWith('http') ? '_blank' : undefined} rel={link.href.startsWith('http') ? 'noreferrer' : undefined} style={{ fontFamily: 'var(--font-mono)', fontSize: '12px', color: 'rgba(255,255,255,0.3)', textDecoration: 'none', transition: 'color 0.2s' }}
                  onMouseEnter={e => (e.currentTarget.style.color = 'rgba(255,255,255,0.6)')}
                  onMouseLeave={e => (e.currentTarget.style.color = 'rgba(255,255,255,0.3)')}>{link.label}</a>
              ))}
            </div>
          </div>
        ))}
      </div>

      {/* bottom row */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <span style={{ fontFamily: 'var(--font-display)', fontSize: '16px', background: 'linear-gradient(135deg, #707070, #B0B0B0, #E0E0E0, #B0B0B0, #707070)', WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent' }}>iris®</span>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'rgba(255,255,255,0.08)', letterSpacing: '0.15em' }}>© iris — fin</span>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'rgba(255,255,255,0.15)', letterSpacing: '0.15em' }}>© 2026 · built at citrus hack</span>
      </div>
    </SectionStage>
  )
}

// ── app ─────────────────────────────────────────────────────────────

function useIntroTimeline() {
  const runIntro = useCallback(() => {

    const tl = gsap.timeline({ defaults: { ease: 'power2.inOut' } })

    // phase 1: ascii loader (handled by React state, not GSAP)
    // phase 2: hero reveal (GSAP takes over after loader completes)

    // simple fade in. everything at once. 1 second.
    gsap.set('[data-intro="logo-dark"]', { opacity: 0 })
    gsap.set('[data-intro="metallic"]', { opacity: 0 })
    gsap.set('[data-intro="chrome"]', { opacity: 0 })
    gsap.set('[data-intro="subtext"]', { opacity: 0 })
    gsap.set('[data-intro="hero-images"]', { opacity: 0 })
    gsap.set('[data-intro="nav"]', { opacity: 0, y: -140 })

    // 1. iris text first
    tl.to('[data-intro="logo-dark"], [data-intro="metallic"]', {
      opacity: 1,
      duration: 0.8,
      ease: 'power2.out',
    })

    // 2. background images
    .to('[data-intro="hero-images"]', {
      opacity: 0.35,
      duration: 0.8,
      ease: 'power2.out',
    }, '-=0.3')

    // 3. text + chrome
    .to('[data-intro="chrome"], [data-intro="subtext"]', {
      opacity: 1,
      duration: 0.6,
      ease: 'power2.out',
    }, '-=0.3')

    // 4. nav last — slides up from below
    .to('[data-intro="nav"]', {
      opacity: 1,
      y: 0,
      duration: 1.35,
      ease: 'power3.out',
    }, '+=0.05')

  }, [])

  return runIntro
}

export default function App() {
  const [loaderDone, setLoaderDone] = useState(false)
  const runIntro = useIntroTimeline()
  const navigate = useNavigate()

  // weighted smooth scroll (framer-style inertia)
  useEffect(() => {
    const lenis = new Lenis({
      duration: 1.4,
      easing: (t: number) => Math.min(1, 1.001 - Math.pow(2, -10 * t)),
      touchMultiplier: 1.5,
      infinite: false,
    })
    function raf(time: number) { lenis.raf(time); requestAnimationFrame(raf) }
    requestAnimationFrame(raf)
    return () => lenis.destroy()
  }, [])

  useEffect(() => {
    if (!loaderDone) return
    requestAnimationFrame(() => {
      requestAnimationFrame(() => { runIntro() })
    })
  }, [loaderDone, runIntro])

  const goStudio = useCallback(() => {
    navigate('/projects')
  }, [navigate])

  return (
    <div style={{ background: '#000', minHeight: '100vh', color: '#fff' }}>
      {/* preload metallic paint shaders during loader (offscreen, invisible) */}
      {!loaderDone && (
        <div aria-hidden style={{ position: 'fixed', width: 1, height: 1, opacity: 0.001, overflow: 'hidden', pointerEvents: 'none', zIndex: -1 }}>
          <MetallicPaint imageSrc={IRIS_SVG} {...FLOWER_METALLIC_PROPS} />
          <MetallicPaint text={IRIS_WORDMARK_TEXT} textOptions={IRIS_WORDMARK_MASK} {...WORDMARK_METALLIC_PROPS} />
        </div>
      )}

      {/* ascii flower loader */}
      <AnimatePresence mode="wait">
        {!loaderDone && <Loader onComplete={() => setLoaderDone(true)} />}
      </AnimatePresence>

      {/* noise */}
      <div style={{ position: 'fixed', inset: 0, zIndex: 90, pointerEvents: 'none', opacity: 0.035 }}>
        <Noise patternSize={256} patternScaleX={1} patternScaleY={1} patternRefreshInterval={2} patternAlpha={15} />
      </div>

      <ScrollFrames dimOpacity={0.4}>
        <FloatingToolbar />
        <Hero onStudio={goStudio} />
        <SectionDivider />
        <Thesis />
        <TechStrip />
        <SectionDivider />
        <Features />
        <SectionDivider />
        <SocialProof />
        <SectionDivider />
        <CTA onStudio={goStudio} />
        <SectionDivider />
        <Footer />
      </ScrollFrames>
    </div>
  )
}
