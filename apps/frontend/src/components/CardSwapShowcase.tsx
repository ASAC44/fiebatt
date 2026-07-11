import { useEffect, useMemo, useRef } from 'react'
import gsap from 'gsap'
import './CardSwapShowcase.css'

type ShowcaseCard = {
  image: string
  label: string
  title: string
  description: string
}

const DEFAULT_CARDS: ShowcaseCard[] = [
  {
    image: '/frames/card-library.gif',
    label: '01 / library',
    title: 'start from your library',
    description: 'Upload a clip or pick up where you left off. Every reel lives here with its edits, variants, and timeline intact.',
  },
  {
    image: '/frames/card-generation.gif',
    label: '02 / select + prompt',
    title: 'draw a box, describe the change',
    description: 'Scrub to the moment, draw a bounding box around what you want to change, and describe the edit in plain language.',
  },
  {
    image: '/frames/card-editing.gif',
    label: '03 / agent + CLI',
    title: 'scriptable by any agent',
    description: 'The iris CLI wraps every editing operation. Any agent that can run bash — Claude Code, Codex, OpenClaw — can edit videos autonomously.',
  },
  {
    image: '/frames/card-vibe.gif',
    label: '04 / agent chat',
    title: 'let the agent drive',
    description: 'Describe what you want in the chat. The agent analyzes your video, identifies regions, generates edits, and shows you the results.',
  },
  {
    image: '/frames/card-export.gif',
    label: '05 / export',
    title: 'from edit to export',
    description: 'When the cut is ready, export the final video. All edits are stitched together into a single MP4.',
  },
]

type CardSwapShowcaseProps = {
  cards?: ShowcaseCard[]
}

const makeSlot = (index: number, total: number) => ({
  x: index * 38,
  y: -index * 24,
  z: -index * 82,
  rotateZ: -index * 1.6,
  zIndex: total - index,
})

export default function CardSwapShowcase({ cards = DEFAULT_CARDS }: CardSwapShowcaseProps) {
  const refs = useRef<Array<HTMLDivElement | null>>([])
  const order = useRef(Array.from({ length: cards.length }, (_, i) => i))
  const intervalRef = useRef<number | null>(null)
  const tlRef = useRef<gsap.core.Timeline | null>(null)

  const safeCards = useMemo(() => cards, [cards])

  useEffect(() => {
    const total = safeCards.length
    refs.current.forEach((el, index) => {
      if (!el) return
      const slot = makeSlot(index, total)
      gsap.set(el, {
        x: slot.x,
        y: slot.y,
        z: slot.z,
        rotateZ: slot.rotateZ,
        xPercent: -50,
        yPercent: -50,
        transformOrigin: 'center center',
        zIndex: slot.zIndex,
        force3D: true,
      })
    })

    const swap = () => {
      if (order.current.length < 2) return

      const [front, ...rest] = order.current
      const frontEl = refs.current[front]
      if (!frontEl) return

      const timeline = gsap.timeline()
      tlRef.current = timeline

      timeline.to(frontEl, {
        y: '+=360',
        rotateZ: '+=3',
        duration: 0.9,
        ease: 'power2.inOut',
      })

      timeline.addLabel('promote', '-=0.4')

      rest.forEach((idx, position) => {
        const el = refs.current[idx]
        if (!el) return
        const slot = makeSlot(position, total)
        timeline.set(el, { zIndex: slot.zIndex }, 'promote')
        timeline.to(el, {
          x: slot.x,
          y: slot.y,
          z: slot.z,
          rotateZ: slot.rotateZ,
          duration: 0.9,
          ease: 'power2.inOut',
        }, `promote+=${position * 0.08}`)
      })

      const backSlot = makeSlot(total - 1, total)
      timeline.addLabel('return', 'promote+=0.16')
      timeline.call(() => {
        gsap.set(frontEl, { zIndex: backSlot.zIndex })
      }, undefined, 'return')
      timeline.to(frontEl, {
        x: backSlot.x,
        y: backSlot.y,
        z: backSlot.z,
        rotateZ: backSlot.rotateZ,
        duration: 0.9,
        ease: 'power2.inOut',
      }, 'return')

      timeline.call(() => {
        order.current = [...rest, front]
      })
    }

    swap()
    intervalRef.current = window.setInterval(swap, 6000)

    return () => {
      tlRef.current?.kill()
      if (intervalRef.current !== null) {
        window.clearInterval(intervalRef.current)
      }
    }
  }, [safeCards])

  return (
    <div className="card-swap-showcase">
      <div className="card-swap-copy">
        <p style={{ fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'rgba(255,255,255,0.22)', letterSpacing: '0.16em', margin: '0 0 24px' }}>
          product workflow
        </p>
        <h3 style={{ fontFamily: 'var(--font-display)', fontSize: 'clamp(40px, 5vw, 72px)', fontWeight: 500, lineHeight: 0.95, letterSpacing: '-0.05em', color: '#fff', margin: '0 0 28px', textTransform: 'lowercase' }}>
          planning,
          <br />
          generation,
          <br />
          continuity.
        </h3>
        <p style={{ fontFamily: 'var(--font-body)', fontSize: '19px', lineHeight: 1.72, color: 'rgba(255,255,255,0.5)', margin: '0 0 28px', maxWidth: '34ch' }}>
          Iris is not a one-shot generator. It layers planning, generation, tracking, and continuity into a single editing workflow.
        </p>
        <p style={{ fontFamily: 'var(--font-body)', fontSize: '16px', lineHeight: 1.8, color: 'rgba(255,255,255,0.38)', margin: '0 0 40px', maxWidth: '38ch' }}>
          Select a moment, preview multiple outcomes, and keep the strongest interpretation moving through the cut with enough continuity to feel authored.
        </p>
        <div style={{ display: 'grid', gap: '14px', maxWidth: '36ch' }}>
          {[
            'Prompt the moment instead of hunting through tool panels.',
            'Review multiple variants before committing to the cut.',
            'Keep motion, continuity, and delivery inside one workflow.',
          ].map((item) => (
            <div key={item} style={{ display: 'flex', alignItems: 'flex-start', gap: '12px' }}>
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'rgba(255,255,255,0.18)', letterSpacing: '0.18em', marginTop: '6px' }}>+</span>
              <p style={{ fontFamily: 'var(--font-body)', fontSize: '14px', lineHeight: 1.72, color: 'rgba(255,255,255,0.4)', margin: 0 }}>
                {item}
              </p>
            </div>
          ))}
        </div>
      </div>

      <div className="card-swap-stage">
        <div
          style={{
            position: 'absolute',
            inset: '8% 2% 0 12%',
            borderRadius: '32px',
            background: 'radial-gradient(circle at 25% 20%, rgba(255,255,255,0.08), transparent 40%), radial-gradient(circle at 80% 80%, rgba(255,255,255,0.05), transparent 45%)',
            filter: 'blur(28px)',
            opacity: 0.7,
            pointerEvents: 'none',
          }}
        />
        <div className="card-swap-stack">
          {safeCards.map((card, index) => (
            <div
              key={`${card.title}-${index}`}
              ref={(node) => { refs.current[index] = node }}
              className="card-swap-item"
            >
              <img className="card-swap-media" src={card.image} alt="" />
              <div className="card-swap-body">
                <div className="card-swap-meta">
                  <span>{card.label}</span>
                  <span>{String(index + 1).padStart(2, '0')}</span>
                </div>
                <h4 className="card-swap-title">{card.title}</h4>
                <p className="card-swap-description">{card.description}</p>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
