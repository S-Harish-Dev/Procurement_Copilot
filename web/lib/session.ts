"use client";

import type { Persona } from "./types";

// The JWT lives in sessionStorage (per tab, gone when the tab closes), never in a cookie: the
// gateway authenticates bearer tokens only, so there is nothing for a CSRF to ride on.
const TOKEN = "pc.token";
const USER = "pc.user";

export interface Session {
  token: string;
  user: Persona;
}

function storage(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.sessionStorage;
  } catch {
    return null;
  }
}

export function loadSession(): Session | null {
  const s = storage();
  const token = s?.getItem(TOKEN);
  const user = s?.getItem(USER);
  if (!token || !user) return null;
  try {
    return { token, user: JSON.parse(user) as Persona };
  } catch {
    return null;
  }
}

export function saveSession(session: Session): void {
  const s = storage();
  s?.setItem(TOKEN, session.token);
  s?.setItem(USER, JSON.stringify(session.user));
}

export function clearSession(): void {
  const s = storage();
  s?.removeItem(TOKEN);
  s?.removeItem(USER);
}
