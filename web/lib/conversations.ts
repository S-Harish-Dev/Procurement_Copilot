"use client";

import type { Proposal, Source } from "./types";

// Conversations live in localStorage per user: chat history is a per-browser convenience, not a
// record (the gateway is stateless per request). Every access is guarded: storage may be blocked.

export interface ActionOutcome {
  status: string;
  message: string;
  prId?: string;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  status?: string;
  requestId?: string;
  sources?: Source[];
  proposals?: Proposal[];
  tools?: string[];
  outcomes?: Record<string, ActionOutcome>;
  feedback?: "up" | "down";
  // Admin-only pipeline details from the `done` event (never sent to other roles).
  diagnostics?: Record<string, unknown>;
  streaming?: boolean;
}

export interface Conversation {
  id: string;
  title: string;
  updatedAt: number;
  messages: ChatMessage[];
  /** Hidden from the main list but kept; restorable or deletable from the Archived section. */
  archived?: boolean;
}

const key = (userId: string) => `pc.conversations.${userId}`;
const MAX = 30;

export function loadConversations(userId: string): Conversation[] {
  try {
    const raw = localStorage.getItem(key(userId));
    return raw ? (JSON.parse(raw) as Conversation[]) : [];
  } catch {
    return [];
  }
}

export function saveConversations(userId: string, all: Conversation[]): void {
  try {
    const trimmed = [...all].sort((a, b) => b.updatedAt - a.updatedAt).slice(0, MAX);
    // Never persist a half-streamed message as if it were final.
    const clean = trimmed.map((c) => ({ ...c, messages: c.messages.map((m) => ({ ...m, streaming: false })) }));
    localStorage.setItem(key(userId), JSON.stringify(clean));
  } catch {
    /* quota or blocked storage: history just isn't kept */
  }
}

/** Wall-clock timestamp for conversation ordering (kept out of components: render must stay pure). */
export const now = () => Date.now();

export const newId = () => (typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : String(Date.now() + Math.random()));
