"use client";

import { clearSession, loadSession } from "./session";
import type { Denied } from "./types";

export const GATEWAY = (process.env.NEXT_PUBLIC_GATEWAY_URL ?? "http://127.0.0.1:8080").replace(/\/$/, "");

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

function authHeaders(): Record<string, string> {
  const s = loadSession();
  return s ? { authorization: `Bearer ${s.token}` } : {};
}

async function parse<T>(res: Response): Promise<T> {
  if (res.status === 401) {
    // Expired or rotated token: back to the persona picker rather than a broken page.
    clearSession();
    if (typeof window !== "undefined" && !window.location.pathname.startsWith("/login")) {
      // Plain module code (no router here); a full navigation also drops every cached query.
      // eslint-disable-next-line @next/next/no-location-assign-relative-destination
      window.location.assign("/login");
    }
  }
  if (!res.ok) {
    let message = `Request failed (${res.status})`;
    try {
      const body = (await res.json()) as { error?: string };
      if (body.error) message = body.error;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, message);
  }
  return (await res.json()) as T;
}

export async function get<T>(path: string): Promise<T> {
  return parse<T>(await fetch(`${GATEWAY}${path}`, { headers: authHeaders() }));
}

export async function post<T>(path: string, body?: unknown): Promise<T> {
  return parse<T>(
    await fetch(`${GATEWAY}${path}`, {
      method: "POST",
      headers: { "content-type": "application/json", ...authHeaders() },
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
  );
}

export function isDenied(v: unknown): v is Denied {
  return typeof v === "object" && v !== null && (v as Denied).status === "denied";
}

/** Call a UI tool. A policy denial is surfaced as an Error with the server's user-facing message. */
export async function tool<T>(name: string, args: Record<string, unknown> = {}): Promise<T> {
  const r = await post<T | Denied>(`/v1/tools/${name}`, { args });
  if (isDenied(r)) throw new ApiError(403, r.message);
  return r as T;
}

/** Authenticated binary download (the PO PDF needs the bearer header, so a plain link won't do). */
export async function download(path: string, filename: string): Promise<void> {
  const res = await fetch(`${GATEWAY}${path}`, { headers: authHeaders() });
  if (!res.ok) throw new ApiError(res.status, res.status === 404 ? "No document is available yet." : "Download failed.");
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
