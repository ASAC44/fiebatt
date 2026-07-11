const ONBOARDING_KEY_PREFIX = "iris.onboarding.completed";
const CHECKLIST_KEY_PREFIX = "iris.editor.checklist.dismissed";

function safeRead(key: string): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function safeWrite(key: string, value: string) {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // storage can be blocked in private browsing / hardened environments.
    // onboarding should degrade, not explode.
  }
}

export function onboardingKey(userId: string) {
  return `${ONBOARDING_KEY_PREFIX}.${userId}`;
}

export function hasCompletedOnboarding(userId: string | null | undefined) {
  if (!userId) return false;
  return safeRead(onboardingKey(userId)) === "1";
}

export function markOnboardingComplete(userId: string | null | undefined) {
  if (!userId) return;
  safeWrite(onboardingKey(userId), "1");
}

export function checklistKey(scope: string) {
  return `${CHECKLIST_KEY_PREFIX}.${scope}`;
}

export function isEditorChecklistDismissed(scope: string) {
  return safeRead(checklistKey(scope)) === "1";
}

export function setEditorChecklistDismissed(scope: string, dismissed: boolean) {
  safeWrite(checklistKey(scope), dismissed ? "1" : "0");
}
