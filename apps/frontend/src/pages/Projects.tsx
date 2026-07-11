import { useEffect, useRef, useState } from 'react'
import { motion, AnimatePresence } from 'motion/react'
import { deleteProject, listProjects, type ProjectListItem } from '../api/client'
import OrganicDarkBackground from '../components/OrganicDarkBackground'
import TiltedCard from '../components/TiltedCard'
import { useAuth } from '../lib/useAuth'
import './projects.css'

const SPRING_BOUNCY = { type: 'spring' as const, stiffness: 350, damping: 40, mass: 1 }
const SPRING_BADGE = { type: 'spring' as const, stiffness: 350, damping: 40, mass: 1.5 }
const BLANK_POSTER =
  "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='16' height='10' viewBox='0 0 16 10'><rect width='16' height='10' fill='%23060606'/></svg>"

export function Projects({
  onExit,
  onOpen,
  onNew,
}: {
  onExit: () => void
  onOpen: (projectId: string) => void
  onNew: () => void
}) {
  const { user } = useAuth()
  const [items, setItems] = useState<ProjectListItem[] | null>(null)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    listProjects()
      .then((r) => { if (alive) setItems(r) })
      .catch((e) => { if (alive) setErr(String(e?.message || e)) })
    return () => { alive = false }
  }, [])

  const display = user?.user_metadata?.full_name
    || user?.user_metadata?.name
    || user?.email?.split('@')[0]
    || 'guest'

  return (
    <div className="projects-page">
      <div className="projects-background">
        <OrganicDarkBackground />
        <div className="projects-background__veil" />
      </div>

      <header className="projects-topbar">
        <div className="projects-topbar__left">
          <button className="projects-brand" onClick={onExit}>
            <span className="projects-brand__mark" aria-hidden />
            <span className="projects-brand__word">iris</span>
          </button>
          <span style={{ width: 1, height: 16, background: 'rgba(255,255,255,0.08)', margin: '0 10px' }} />
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'rgba(255,255,255,0.3)', letterSpacing: '0.14em' }}>library</span>
        </div>

        <div className="projects-topbar__right">
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: '10px', color: 'rgba(255,255,255,0.25)', letterSpacing: '0.1em' }}>
            {String(display).toLowerCase()}
          </span>
          <button className="projects-create" onClick={onNew}>
            <span className="projects-create__icon">+</span>
            <span>new reel</span>
          </button>
        </div>
      </header>

      <motion.section
        className="projects-header"
        initial={{ opacity: 0, y: 30 }}
        animate={{ opacity: 1, y: 0 }}
        transition={SPRING_BADGE}
      >
        <div className="projects-sectionnum">002 / library</div>
        <h1 className="projects-title">
          your <span className="chrome">videos</span>
        </h1>
        <div className="projects-subhead">
          <p>
            every reel you've cut lives here. pick one up where you left off,
            or start a fresh edit - iris remembers nothing you didn't teach it.
          </p>
          <div className="projects-count">
            {items === null ? '-' : String(items.length).padStart(3, '0')} videos
          </div>
        </div>
      </motion.section>

      <AnimatePresence mode="wait">
        {items === null && !err && (
          <motion.div
            key="loading"
            className="projects-spinner"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
          >
            <span className="dot" />
            <span>loading library</span>
          </motion.div>
        )}

        {err && (
          <motion.div
            key="error"
            className="projects-empty"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
          >
            <h3>couldn't load library</h3>
            <div style={{ color: 'rgba(255,100,100,0.7)' }}>{err}</div>
          </motion.div>
        )}

        {items !== null && !err && (
          <motion.section
            key="grid"
            className="projects-grid"
            initial="hidden"
            animate="show"
            variants={{
              hidden: {},
              show: { transition: { staggerChildren: 0.06, delayChildren: 0.15 } },
            }}
          >
            <motion.button
              className="proj-tilt-shell"
              onClick={onNew}
              variants={{
                hidden: { opacity: 0, y: 24 },
                show: { opacity: 1, y: 0, transition: SPRING_BOUNCY },
              }}
              whileTap={{ scale: 0.985 }}
            >
              <TiltedCard
                imageSrc={BLANK_POSTER}
                altText="new reel"
                captionText="new reel"
                containerHeight="100%"
                containerWidth="100%"
                imageHeight="100%"
                imageWidth="100%"
                rotateAmplitude={8}
                scaleOnHover={1.04}
                showMobileWarning={false}
                showTooltip
                displayOverlayContent
                className="proj-tilt-card"
                overlayClassName="proj-tilt-overlay"
                overlayContent={(
                  <div className="proj-new">
                    <div className="proj-new__plus">+</div>
                    <div>new reel</div>
                  </div>
                )}
              />
            </motion.button>

            {items.map((p, i) => (
              <ProjectCard
                key={p.project_id}
                project={p}
                index={i}
                onOpen={() => onOpen(p.project_id)}
                onDelete={() => {
                  deleteProject(p.project_id)
                    .then(() => setItems(prev => prev?.filter(x => x.project_id !== p.project_id) ?? null))
                    .catch(e => console.error('delete failed:', e))
                }}
              />
            ))}
          </motion.section>
        )}
      </AnimatePresence>
    </div>
  )
}

function ProjectCard({
  project,
  index,
  onOpen,
  onDelete,
}: {
  project: ProjectListItem
  index: number
  onOpen: () => void
  onDelete: () => void
}) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const [ready, setReady] = useState(false)

  function handleEnter() {
    const v = videoRef.current
    if (!v) return
    v.currentTime = 0
    v.play().catch(() => {})
  }

  function handleLeave() {
    const v = videoRef.current
    if (!v) return
    v.pause()
    v.currentTime = 0
  }

  const created = formatRelative(project.created_at)
  const idShort = project.project_id.slice(0, 8)
  const dur = formatDuration(project.duration)
  const res = `${project.width}x${project.height}`

  return (
    <motion.div
      className="proj-tilt-shell"
      variants={{
        hidden: { opacity: 0, y: 24 },
        show: { opacity: 1, y: 0, transition: SPRING_BOUNCY },
      }}
      transition={SPRING_BOUNCY}
    >
      <TiltedCard
        imageSrc={BLANK_POSTER}
        altText={`project ${idShort}`}
        captionText={`${idShort} · ${dur}`}
        containerHeight="100%"
        containerWidth="100%"
        imageHeight="100%"
        imageWidth="100%"
        rotateAmplitude={8}
        scaleOnHover={1.035}
        showMobileWarning={false}
        showTooltip
        displayOverlayContent
        className="proj-tilt-card"
        overlayClassName="proj-tilt-overlay"
        onClick={onOpen}
        onMouseEnter={handleEnter}
        onMouseLeave={handleLeave}
        overlayContent={(
          <div className="proj-card">
            <video
              ref={videoRef}
              className="proj-card__video"
              src={project.video_url}
              preload="metadata"
              muted
              loop
              playsInline
              onLoadedMetadata={(e) => {
                const v = e.currentTarget
                if (v.currentTime === 0) v.currentTime = 0.05
              }}
              onSeeked={() => setReady(true)}
              onLoadedData={() => setReady(true)}
              style={{ opacity: ready ? 1 : 0, transition: 'opacity 0.4s ease' }}
            />
            {!ready && <div className="proj-card__poster" />}

            <div className="proj-card__glow" />
            <div className="proj-card__scrim" />

            <div className="proj-card__corner">
              reel · {String(index + 1).padStart(3, '0')}
            </div>

            <button
              className="proj-card__delete"
              onClick={(e) => { e.stopPropagation(); onDelete() }}
              title="delete project"
            >
              ×
            </button>

            <div className="proj-card__meta">
              <div>
                <div className="proj-card__id">{idShort}</div>
                <div className="proj-card__created">{created}</div>
              </div>
              <div className="proj-card__stats">
                <span>{dur}</span>
                <span>{res} · {Math.round(project.fps)}fps</span>
              </div>
            </div>
          </div>
        )}
      />
    </motion.div>
  )
}

function formatDuration(sec: number): string {
  if (!isFinite(sec) || sec < 0) return '0:00'
  const m = Math.floor(sec / 60)
  const s = Math.floor(sec % 60)
  return `${m}:${String(s).padStart(2, '0')}`
}

function formatRelative(iso: string): string {
  try {
    const then = new Date(iso).getTime()
    if (Number.isNaN(then)) return ''
    const diff = Date.now() - then
    const min = Math.floor(diff / 60000)
    if (min < 1) return 'just now'
    if (min < 60) return `${min}m ago`
    const hr = Math.floor(min / 60)
    if (hr < 24) return `${hr}h ago`
    const day = Math.floor(hr / 24)
    if (day < 7) return `${day}d ago`
    const wk = Math.floor(day / 7)
    if (wk < 5) return `${wk}w ago`
    return new Date(iso).toLocaleDateString(undefined, {
      month: 'short', day: 'numeric', year: '2-digit',
    }).toLowerCase()
  } catch {
    return ''
  }
}
