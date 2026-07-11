import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Projects } from '../pages/Projects';
import { useAuth } from '../lib/useAuth';
import {
  hasCompletedOnboarding,
  markOnboardingComplete,
} from '../features/onboarding/storage';

export function ProjectsRoute() {
  const { status, user, signInWithGoogle } = useAuth();
  const navigate = useNavigate();
  const [onboardingDone, setOnboardingDone] = useState(() => hasCompletedOnboarding(user?.id));

  useEffect(() => {
    setOnboardingDone(hasCompletedOnboarding(user?.id));
  }, [user?.id]);

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

  return (
    <Projects
      onExit={() => navigate('/')}
      onNew={() => navigate('/editor')}
      onOpen={(projectId) => navigate(`/editor/${projectId}`)}
    />
  );
}
