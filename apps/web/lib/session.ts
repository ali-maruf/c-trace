// The browser session: a bearer token for the C-Trace API. Demo sandboxes get a C-Trace demo token;
// real users get a Supabase access token. Kept in sessionStorage (per tab, gone when the tab closes).
export type Session = { token: string; kind: "demo" | "supabase"; expiresAt: number; orgId?: string; factoryId?: string };

const KEY = "ctrace.session";
const listeners = new Set<() => void>();

export function getSession(): Session | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.sessionStorage.getItem(KEY);
    if (!raw) return null;
    const s = JSON.parse(raw) as Session;
    if (s.expiresAt <= Date.now()) {
      window.sessionStorage.removeItem(KEY);
      return null;
    }
    return s;
  } catch {
    return null;
  }
}

export function setSession(s: Session): void {
  window.sessionStorage.setItem(KEY, JSON.stringify(s));
  listeners.forEach((l) => l());
}

export function clearSession(): void {
  window.sessionStorage.removeItem(KEY);
  listeners.forEach((l) => l());
}

export function onSessionChange(fn: () => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}
