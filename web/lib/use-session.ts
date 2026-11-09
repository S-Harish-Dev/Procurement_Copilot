"use client";

import { useSyncExternalStore } from "react";
import { clearSession, loadSession, saveSession, type Session } from "./session";

// sessionStorage is browser-only; useSyncExternalStore gives a server snapshot (null) and the real
// value after hydration without a setState-in-effect flash.
const listeners = new Set<() => void>();
let cached: { raw: string; value: Session | null } = { raw: "", value: null };

function snapshot(): Session | null {
  const s = loadSession();
  const raw = s ? `${s.token}|${s.user.id}` : "";
  if (raw !== cached.raw) cached = { raw, value: s };
  return cached.value;
}

function subscribe(cb: () => void) {
  listeners.add(cb);
  return () => listeners.delete(cb);
}

const notify = () => listeners.forEach((l) => l());

export function useSession(): Session | null {
  return useSyncExternalStore(subscribe, snapshot, () => null);
}

/** True once the client has read storage (distinguishes "logged out" from "not hydrated yet"). */
export function useHydrated(): boolean {
  return useSyncExternalStore(
    () => () => {},
    () => true,
    () => false,
  );
}

export function signIn(session: Session) {
  saveSession(session);
  notify();
}

export function signOut() {
  clearSession();
  notify();
}
