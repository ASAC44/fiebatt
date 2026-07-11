import { useState, useEffect } from 'react';
import { Navigate, useParams, useNavigate } from 'react-router-dom';
import { motion, AnimatePresence } from 'motion/react';
import { Studio } from '../pages/Studio';
import { getProject, listProjects, type ProjectDetail } from '../api/client';
import { useAuth } from '../lib/useAuth';
import {
  hasCompletedOnboarding,
  markOnboardingComplete,
} from '../features/onboarding/storage';

function EditorLoader({ onDone }: { onDone: () => void }) {
  const [progress, setProgress] = useState(0);

  useEffect(() => {
    const i = setInterval(() => {
      setProgress(p => {
        if (p >= 100) { clearInterval(i); setTimeout(onDone, 300); return 100; }
        return Math.min(p + (p < 40 ? 4 : p < 75 ? 3 : 2), 100);
      });
    }, 25);
    return () => clearInterval(i);
  }, [onDone]);

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.4 }}
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 200,
        background: '#000',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        gap: '24px',
      }}
    >
      <motion.span
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5, delay: 0.1 }}
        style={{
          fontFamily: 'var(--font-display, Georgia)',
          fontStyle: 'italic',
          fontSize: '24px',
          background: 'linear-gradient(135deg, #707070, #B0B0B0, #E0E0E0, #B0B0B0, #707070)',
          backgroundSize: '200% 100%',
          WebkitBackgroundClip: 'text',
          WebkitTextFillColor: 'transparent',
          animation: 'shimmer 4s ease-in-out infinite',
        }}
      >
        iris.
      </motion.span>

      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ delay: 0.3 }}
        style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '8px' }}
      >
        <div style={{
          width: '160px',
          height: '1px',
          background: 'rgba(255,255,255,0.08)',
          position: 'relative',
          overflow: 'hidden',
        }}>
          <motion.div
            style={{
              position: 'absolute',
              inset: 0,
              right: 'auto',
              width: `${progress}%`,
              background: 'rgba(255,255,255,0.3)',
            }}
          />
        </div>
        <span style={{
          fontFamily: 'var(--font-mono, monospace)',
          fontSize: '10px',
          color: 'rgba(255,255,255,0.2)',
          letterSpacing: '0.15em',
        }}>
          loading editor
        </span>
      </motion.div>
    </motion.div>
  );
}

export function EditorRoute() {
  const { status, user, signInWithGoogle } = useAuth();
  const { projectId } = useParams();
  const navigate = useNavigate();
  const [ready, setReady] = useState(false);
  const [project, setProject] = useState<ProjectDetail | null>(null);
  const [projectErr, setProjectErr] = useState<string | null>(null);
  const [onboardingDone, setOnboardingDone] = useState(() => hasCompletedOnboarding(user?.id));

  useEffect(() => {
    setOnboardingDone(hasCompletedOnboarding(user?.id));
  }, [user?.id]);

  useEffect(() => {
    if (status !== 'authed') {
      setProject(null);
      setProjectErr(null);
      return;
    }

    if (!projectId) {
      setProject(null);
      setProjectErr(null);
      return;
    }

    let cancelled = false;
    setProject(null);
    setProjectErr(null);

    Promise.all([
      getProject(projectId),
      listProjects().catch(() => null),
    ])
      .then(([detail, items]) => {
        if (cancelled) return;
        const listMatch = items?.find((item) => item.project_id === projectId);
        setProject({
          ...detail,
          video_url: listMatch?.video_url ?? detail.video_url,
          duration: listMatch?.duration ?? detail.duration,
          fps: listMatch?.fps ?? detail.fps,
        });
      })
      .catch((err) => {
        if (!cancelled) setProjectErr(String(err?.message || err));
      });

    return () => {
      cancelled = true;
    };
  }, [projectId, status]);

  if (status === 'loading') return null;
  if (status === 'anon') {
    // skip the gate — go straight to google sign-in
    void signInWithGoogle();
    return null;
  }

  // auto-complete onboarding — no intermediary screens
  if (!onboardingDone) {
    markOnboardingComplete(user?.id);
    setOnboardingDone(true);
  }

  const loadingProject = !!projectId && !project && !projectErr;

  return (
    <>
      <AnimatePresence mode="wait">
        {(!ready || loadingProject) && <EditorLoader onDone={() => setReady(true)} />}
      </AnimatePresence>

      {ready && projectErr && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          style={{
            minHeight: '100vh',
            background: '#000',
            color: 'rgba(255,255,255,0.84)',
            display: 'grid',
            placeItems: 'center',
            padding: '32px',
          }}
        >
          <div style={{ maxWidth: 520, textAlign: 'center' }}>
            <div style={{ fontSize: '28px', marginBottom: '12px' }}>couldn't reopen this reel</div>
            <div style={{ color: 'rgba(255,255,255,0.48)', marginBottom: '20px' }}>{projectErr}</div>
            <button
              onClick={() => navigate('/projects')}
              style={{
                border: '1px solid rgba(255,255,255,0.14)',
                background: 'rgba(255,255,255,0.06)',
                color: '#fff',
                padding: '10px 16px',
                cursor: 'pointer',
              }}
            >
              back to library
            </button>
          </div>
        </motion.div>
      )}

      {ready && !loadingProject && !projectErr && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.5 }}
        >
          <Studio
            key={project?.project_id ?? 'new'}
            onExit={() => navigate('/')}
            onLibrary={() => navigate('/projects')}
            initialProject={
              project
                ? {
                    projectId: project.project_id,
                    videoUrl: project.video_url,
                    duration: project.duration,
                    fps: project.fps,
                  }
                : undefined
            }
          />
        </motion.div>
      )}
    </>
  );
}
