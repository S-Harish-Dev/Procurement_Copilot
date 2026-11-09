"use client";

import { Crumbs } from "@/components/chat/source-meta";
import { ArchiveRestore, BookOpen, ClipboardCheck, Copy, FileText, Library, MessageSquarePlus, PanelLeft, RotateCcw, Send, Sparkles, Square, ThumbsDown, ThumbsUp, Trash2, Wallet, Wrench, X, type LucideIcon } from "lucide-react";
import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from "react";
import { ActionCard } from "@/components/chat/action-card";
import { Answer } from "@/components/chat/answer";
import { Badge, TONE, type Tone } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { RichText } from "@/components/ui/rich-text";
import { Sheet } from "@/components/ui/sheet";
import { useToast } from "@/components/ui/toast";
import { cancelAction, confirmAction } from "@/lib/actions";
import { GATEWAY, post } from "@/lib/api";
import { loadConversations, newId, now, saveConversations, type ChatMessage, type Conversation } from "@/lib/conversations";
import { SUGGESTED } from "@/lib/prompts";
import { streamSse } from "@/lib/sse";
import { useSession } from "@/lib/use-session";
import type { Proposal, Source } from "@/lib/types";

// Write tools only ever *prepare* an action for confirmation; say so instead of "looked up".
const WRITE_TOOL_LABEL: Record<string, string> = {
  create_purchase_request: "Prepared a purchase request for your review",
  submit_purchase_request: "Prepared a submission for your review",
  approve_request: "Prepared an approval for your review",
  reject_request: "Prepared a rejection for your review",
};

const TOOL_LABEL: Record<string, string> = {
  list_my_requests: "your requests",
  get_purchase_request: "a purchase request",
  get_approval_chain: "the approval chain",
  list_pending_approvals: "pending approvals",
  get_budget: "the budget",
  get_spend_summary: "spending",
  search_vendors: "vendors",
  get_vendor: "a vendor",
};

// Suggested prompts are tinted by what they do, so the empty state reads as three kinds of help.
function kindOf(q: string): { icon: LucideIcon; tone: Tone; label: string } {
  if (/approv|pending/i.test(q)) return { icon: ClipboardCheck, tone: "warn", label: "Approvals" };
  if (/budget|spend|spent|request/i.test(q)) return { icon: Wallet, tone: "good", label: "Your data" };
  return { icon: BookOpen, tone: "info", label: "Policy" };
}

// Final state of a turn, shown only when it isn't a normal answer.
const OUTCOME: Record<string, { tone: Tone; label: string }> = {
  no_answer: { tone: "warn", label: "No supporting document found" },
  error: { tone: "crit", label: "Something went wrong" },
  stopped: { tone: "neutral", label: "Stopped" },
};

const EMPTY_TURN ="I couldn’t complete that request. Try rephrasing it, or ask about a specific request or policy.";

function httpProblem(status: number): string {
  if (status === 429) return "You’re sending messages quickly. Please wait a few seconds and try again.";
  if (status === 401) return "Your session expired. Please sign in again.";
  return "The assistant is temporarily unavailable. Please try again shortly.";
}

export default function ChatPage() {
  const session = useSession()!; // the (app) layout renders children only with a session
  const user = session.user;
  const toast = useToast();
  const [conversations, setConversations] = useState<Conversation[]>(() => loadConversations(user.id));
  const [activeId, setActiveId] = useState<string | null>(() => conversations.find((c) => !c.archived)?.id ?? null);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [source, setSource] = useState<Source | null>(null);
  const [sidebar, setSidebar] = useState(false);
  const abort = useRef<AbortController | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  const active = conversations.find((c) => c.id === activeId) ?? null;
  const live = conversations.filter((c) => !c.archived);
  const archived = conversations.filter((c) => c.archived);

  // Archive hides a conversation without deleting it; Undo (in the toast) and the Archived list bring it back.
  const setArchived = (id: string, value: boolean) => setConversations((all) => all.map((c) => (c.id === id ? { ...c, archived: value } : c)));
  const restore = (id: string) => setArchived(id, false);
  const removeForever = (id: string) => setConversations((all) => all.filter((c) => c.id !== id));
  function archive(id: string) {
    setArchived(id, true);
    if (activeId === id) setActiveId(null);
    toast({
      title: "Conversation archived",
      body: (
        <button type="button" onClick={() => restore(id)} className="font-medium text-info-text underline underline-offset-4">
          Undo
        </button>
      ),
      tone: "info",
    });
  }

  useEffect(() => {
    saveConversations(user.id, conversations);
  }, [user.id, conversations]);
  const messageCount = active?.messages.length ?? 0;
  const lastContent = active?.messages.at(-1)?.content;
  // Follow the stream: scroll when a message arrives or the last one grows. Block body on purpose:
  // scrollIntoView returns a Promise in current Chromium, and an effect must not return one.
  useEffect(() => {
    void bottom.current?.scrollIntoView({ block: "end" });
  }, [messageCount, lastContent]);

  const patchMessage = useCallback((convId: string, msgId: string, fn: (m: ChatMessage) => ChatMessage) => {
    setConversations((all) =>
      all.map((c) => (c.id !== convId ? c : { ...c, updatedAt: now(), messages: c.messages.map((m) => (m.id === msgId ? fn(m) : m)) })),
    );
  }, []);

  async function send(text: string, opts: { regenerate?: boolean } = {}) {
    const message = text.trim();
    if (!message || streaming) return;
    let conv = active;
    if (!conv) {
      conv = { id: newId(), title: message.slice(0, 60), updatedAt: now(), messages: [] };
    }
    const convId = conv.id;
    const prior = opts.regenerate ? conv.messages.slice(0, -1) : conv.messages;
    const history = prior
      .filter((m) => !m.streaming && m.content)
      .slice(-8)
      .map((m) => ({ role: m.role, content: m.content }));
    const userMsg: ChatMessage = { id: newId(), role: "user", content: message };
    const asst: ChatMessage = { id: newId(), role: "assistant", content: "", streaming: true };
    const messages = opts.regenerate ? [...conv.messages.slice(0, -1), asst] : [...conv.messages, userMsg, asst];
    const next: Conversation = { ...conv, updatedAt: now(), messages };
    setConversations((all) => [next, ...all.filter((c) => c.id !== convId)]);
    setActiveId(convId);
    setInput("");
    setStreaming(true);

    const controller = new AbortController();
    abort.current = controller;
    try {
      const status = await streamSse(
        `${GATEWAY}/v1/chat`,
        { body: { message, history }, token: session.token, signal: controller.signal },
        ({ event, data }) => {
          const d = data as Record<string, unknown>;
          patchMessage(convId, asst.id, (m) => {
            switch (event) {
              case "meta":
                return { ...m, requestId: String(d.request_id ?? "") };
              case "delta":
                return { ...m, content: m.content + String(d.text ?? "") };
              case "replace":
                return { ...m, content: String(d.text ?? ""), sources: [] };
              case "sources":
                return { ...m, sources: data as Source[] };
              case "tool":
                // Each call reports phase "started" then "done"; one chip per call.
                return d.phase === "done" ? m : { ...m, tools: [...(m.tools ?? []), String(d.name ?? "a tool")] };
              case "proposal":
                return { ...m, proposals: [...(m.proposals ?? []), data as Proposal] };
              case "done":
                return {
                  ...m,
                  // A finished turn with neither text nor an action card must still say something.
                  content: (m.content || m.proposals?.length) ? m.content : EMPTY_TURN,
                  status: String(d.status ?? ""),
                  diagnostics: (d.diagnostics as Record<string, unknown> | undefined) ?? undefined,
                };
              default:
                return m;
            }
          });
        },
      );
      if (status !== 200) patchMessage(convId, asst.id, (m) => ({ ...m, content: httpProblem(status), status: "error" }));
    } catch (e) {
      const aborted = e instanceof DOMException && e.name === "AbortError";
      patchMessage(convId, asst.id, (m) => ({
        ...m,
        content: aborted ? m.content || "Stopped." : httpProblem(0),
        status: aborted ? "stopped" : "error",
      }));
    } finally {
      patchMessage(convId, asst.id, (m) => ({ ...m, streaming: false }));
      setStreaming(false);
      abort.current = null;
      inputRef.current?.focus();
    }
  }

  async function confirm(convId: string, msgId: string, p: Proposal) {
    const outcome = await confirmAction(p);
    patchMessage(convId, msgId, (m) => ({ ...m, outcomes: { ...m.outcomes, [p.proposal_id]: outcome } }));
    const ok = outcome.status === "executed";
    toast({ title: ok ? "Action completed" : "Action not completed", body: outcome.message, tone: ok ? "success" : "error" });
  }

  async function cancel(convId: string, msgId: string, p: Proposal) {
    const outcome = await cancelAction(p);
    patchMessage(convId, msgId, (m) => ({ ...m, outcomes: { ...m.outcomes, [p.proposal_id]: outcome } }));
  }

  async function feedback(convId: string, m: ChatMessage, rating: "up" | "down", question: string) {
    patchMessage(convId, m.id, (x) => ({ ...x, feedback: rating }));
    try {
      await post("/v1/feedback", { request_id: m.requestId, rating, question, answer: m.content });
      toast({ title: "Thanks for the feedback", tone: "success" });
    } catch {
      patchMessage(convId, m.id, (x) => ({ ...x, feedback: undefined }));
      toast({ title: "Feedback couldn’t be saved", body: "Please try again later.", tone: "error" });
    }
  }

  function onKey(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void send(input);
    }
  }

  const lastAssistant = active?.messages.findLast((m) => m.role === "assistant");

  return (
    // Fills the viewport under the nav (89px = nav + gutter) down to a ~15px margin; -mb-5 cancels <main>'s bottom padding so the page itself never scrolls.
    <div className="-mb-5 grid h-[calc(100dvh-6.5rem)] grid-cols-1 gap-4 md:grid-cols-[16rem_1fr]">
      <aside className={`${sidebar ? "block" : "hidden"} min-h-0 overflow-y-auto rounded-lg border border-line bg-surface p-2 md:block`} aria-label="Conversations">
        <Button
          variant="secondary"
          className="mb-2 w-full"
          onClick={() => {
            setActiveId(null);
            setSidebar(false);
            inputRef.current?.focus();
          }}
        >
          <MessageSquarePlus className="size-4" aria-hidden /> New conversation
        </Button>
        {live.length === 0 ? (
          <p className="px-2 py-3 text-sm text-ink-2">Your conversations will appear here.</p>
        ) : (
          <ul className="space-y-0.5">
            {live.map((c) => (
              <li key={c.id} className="group relative">
                <button
                  type="button"
                  onClick={() => {
                    setActiveId(c.id);
                    setSidebar(false);
                  }}
                  aria-current={c.id === activeId ? "true" : undefined}
                  className={`w-full truncate rounded-lg py-2 pr-10 pl-2.5 text-left text-sm ${c.id === activeId ? "bg-info-soft font-medium text-info-text" : "text-ink-2 hover:bg-surface-2 hover:text-ink"}`}
                >
                  {c.title}
                </button>
                {/* Bin appears on hover or keyboard focus (always visible on touch, where there is no hover). */}
                <button
                  type="button"
                  onClick={() => archive(c.id)}
                  aria-label={`Archive conversation: ${c.title}`}
                  title="Archive"
                  className="absolute top-1/2 right-1 inline-flex size-8 -translate-y-1/2 items-center justify-center rounded-md text-ink-2 transition-opacity hover:bg-crit-soft hover:text-danger-text focus-visible:opacity-100 md:opacity-0 md:group-focus-within:opacity-100 md:group-hover:opacity-100"
                >
                  <Trash2 aria-hidden className="size-4" />
                </button>
              </li>
            ))}
          </ul>
        )}
        {archived.length > 0 && (
          <details className="mt-3 border-t border-line pt-2">
            <summary className="cursor-pointer rounded-lg px-2.5 py-1.5 text-sm text-ink-2 hover:bg-surface-2 hover:text-ink">Archived ({archived.length})</summary>
            <ul className="mt-1 space-y-0.5">
              {archived.map((c) => (
                <li key={c.id} className="flex items-center gap-1 rounded-lg pl-2.5 text-sm text-ink-2 hover:bg-surface-2">
                  <span className="min-w-0 flex-1 truncate">{c.title}</span>
                  <button type="button" onClick={() => restore(c.id)} aria-label={`Restore: ${c.title}`} title="Restore" className="inline-flex size-8 items-center justify-center rounded-md hover:bg-info-soft hover:text-info-text">
                    <ArchiveRestore aria-hidden className="size-4" />
                  </button>
                  <button type="button" onClick={() => removeForever(c.id)} aria-label={`Delete permanently: ${c.title}`} title="Delete permanently" className="inline-flex size-8 items-center justify-center rounded-md hover:bg-crit-soft hover:text-danger-text">
                    <X aria-hidden className="size-4" />
                  </button>
                </li>
              ))}
            </ul>
          </details>
        )}
      </aside>

      <section className="flex min-h-0 flex-col rounded-lg border border-line bg-surface" aria-label="Conversation">
        <div className="flex items-center gap-2 border-b border-line px-3 py-2 md:hidden">
          <Button size="sm" variant="ghost" onClick={() => setSidebar((s) => !s)} aria-expanded={sidebar}>
            <PanelLeft className="size-4" aria-hidden /> Conversations
          </Button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-5" aria-live="polite" aria-busy={streaming}>
          {!active || active.messages.length === 0 ? (
            <div className="mx-auto max-w-2xl py-10 text-center">
              <span aria-hidden className="inline-flex size-12 items-center justify-center rounded-2xl bg-btn text-white shadow-sm">
                <Sparkles className="size-6" />
              </span>
              <h1 className="mt-4 text-2xl font-semibold tracking-tight">Hi {user.name.split(" ")[0]}, how can I help?</h1>
              <p className="mx-auto mt-1.5 max-w-xl text-sm text-ink-2 text-pretty">
                Ask about Acme’s procurement policies and contracts, check your requests, or start a purchase. I only use
                documents you’re allowed to see, and I’ll always ask before changing anything.
              </p>
              <ul className="mt-6 grid gap-3 sm:grid-cols-2">
                {SUGGESTED[user.role].map((q) => {
                  const k = kindOf(q);
                  return (
                    <li key={q}>
                      <button type="button" onClick={() => void send(q)} className="group flex h-full w-full items-start gap-3 rounded-xl border border-line bg-surface p-3.5 text-left text-sm shadow-[0_1px_2px_rgba(11,11,11,0.05)] transition-[border-color,box-shadow] hover:border-btn hover:shadow-md">
                        <span aria-hidden className={`inline-flex size-9 shrink-0 items-center justify-center rounded-lg ${TONE[k.tone]}`}>
                          <k.icon className="size-4.5" />
                        </span>
                        <span>
                          <span className="block text-xs font-medium text-ink-2">{k.label}</span>
                          {q}
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            </div>
          ) : (
            <ol className="mx-auto max-w-3xl space-y-6">
              {active.messages.map((m, i) => {
                const question = active.messages[i - 1]?.content ?? "";
                return (
                  <li key={m.id} className={`rise ${m.role === "user" ? "flex justify-end" : "flex gap-3"}`}>
                    {m.role === "user" ? (
                      <div className="max-w-[85%] rounded-2xl rounded-br-md bg-btn px-4 py-2.5 whitespace-pre-wrap text-white shadow-sm">{m.content}</div>
                    ) : (
                      <>
                      <span aria-hidden className="mt-0.5 inline-flex size-8 shrink-0 items-center justify-center rounded-full bg-info-soft text-info-text">
                        <Sparkles className="size-4" />
                      </span>
                      <div className="min-w-0 flex-1">
                        {m.tools && m.tools.length > 0 && (
                          <p className="mb-2 flex flex-wrap gap-1.5">
                            {m.tools.map((t, j) => (
                              <Badge key={j} tone={WRITE_TOOL_LABEL[t] ? "warn" : "info"} icon={Wrench}>
                                {WRITE_TOOL_LABEL[t] ?? `Looked up ${TOOL_LABEL[t] ?? t.replaceAll("_", " ")}`}
                              </Badge>
                            ))}
                          </p>
                        )}
                        {!m.streaming && m.status && OUTCOME[m.status] && (
                          <p className="mb-2">
                            <Badge tone={OUTCOME[m.status].tone}>{OUTCOME[m.status].label}</Badge>
                          </p>
                        )}
                        {m.streaming && !m.content && !m.proposals?.length ? (
                          <div className="space-y-2" aria-label="The assistant is answering">
                            <div className="h-3.5 w-3/4 animate-pulse rounded bg-surface-2" />
                            <div className="h-3.5 w-2/3 animate-pulse rounded bg-surface-2" />
                            <div className="h-3.5 w-1/2 animate-pulse rounded bg-surface-2" />
                          </div>
                        ) : (
                          <Answer text={m.content} sources={m.sources ?? []} onOpen={setSource} />
                        )}
                        {m.proposals?.map((p) => (
                          <ActionCard
                            key={p.proposal_id}
                            proposal={p}
                            outcome={m.outcomes?.[p.proposal_id]}
                            onConfirm={() => confirm(active.id, m.id, p)}
                            onCancel={() => cancel(active.id, m.id, p)}
                          />
                        ))}
                        {m.sources && m.sources.length > 0 && (
                          <div className="mt-3">
                            <p className="mb-1.5 text-xs font-medium text-ink-2">Sources</p>
                            <ul className="grid gap-2 sm:grid-cols-2">
                              {m.sources.map((s) => (
                                <li key={s.id} className="min-w-0">
                                  <button type="button" onClick={() => setSource(s)} className="flex w-full items-center gap-2.5 rounded-xl border border-line bg-surface p-2 text-left text-xs transition-colors hover:border-btn hover:bg-info-soft">
                                    <span aria-hidden className="inline-flex size-7 shrink-0 items-center justify-center rounded-lg bg-info-soft text-sm font-semibold text-info-text">
                                      {s.id.replace("S", "")}
                                    </span>
                                    <span className="min-w-0">
                                      <span className="block truncate text-sm font-medium">{s.title}</span>
                                      <span className="block truncate text-ink-2">{s.section_path.split("›").at(-1)?.trim() || s.source}</span>
                                    </span>
                                  </button>
                                </li>
                              ))}
                            </ul>
                          </div>
                        )}
                        {!m.streaming && m.content && (
                          <div className="mt-2 flex items-center gap-1 text-ink-2">
                            <Button size="sm" variant="ghost" aria-label="Copy answer" onClick={() => void navigator.clipboard?.writeText(m.content)}>
                              <Copy className="size-4" aria-hidden />
                            </Button>
                            {m.id === lastAssistant?.id && (
                              <Button size="sm" variant="ghost" aria-label="Regenerate answer" onClick={() => void send(question, { regenerate: true })} disabled={streaming}>
                                <RotateCcw className="size-4" aria-hidden />
                              </Button>
                            )}
                            {m.requestId && (
                              <>
                                <Button size="sm" variant="ghost" aria-label="Helpful" aria-pressed={m.feedback === "up"} onClick={() => void feedback(active.id, m, "up", question)} disabled={!!m.feedback}>
                                  <ThumbsUp className="size-4" aria-hidden style={m.feedback === "up" ? { color: "var(--status-good)" } : undefined} />
                                </Button>
                                <Button size="sm" variant="ghost" aria-label="Not helpful" aria-pressed={m.feedback === "down"} onClick={() => void feedback(active.id, m, "down", question)} disabled={!!m.feedback}>
                                  <ThumbsDown className="size-4" aria-hidden style={m.feedback === "down" ? { color: "var(--status-critical)" } : undefined} />
                                </Button>
                              </>
                            )}
                          </div>
                        )}
                        {user.role === "admin" && m.diagnostics && (
                          <details className="mt-2 text-xs text-ink-2">
                            <summary className="cursor-pointer">Technical details (admin)</summary>
                            <pre className="mt-1 overflow-x-auto rounded bg-surface-2 p-2">{JSON.stringify(m.diagnostics, null, 2)}</pre>
                          </details>
                        )}
                      </div>
                      </>
                    )}
                  </li>
                );
              })}
            </ol>
          )}
          <div ref={bottom} />
        </div>
        <form
          className="border-t border-line bg-surface-2/40 p-3"
          onSubmit={(e) => {
            e.preventDefault();
            void send(input);
          }}
        >
          <div className="mx-auto flex max-w-3xl items-end gap-2">
            <label htmlFor="chat-input" className="sr-only">
              Message
            </label>
            <textarea
              id="chat-input"
              ref={inputRef}
              rows={1}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={onKey}
              placeholder="Ask a question or describe what you need to buy…"
              className="max-h-40 min-h-10 flex-1 resize-y rounded-xl border border-line bg-surface px-3.5 py-2.5 text-sm placeholder:text-ink-2 focus-visible:border-btn"
            />
            {streaming ? (
              <Button variant="secondary" onClick={() => abort.current?.abort()} aria-label="Stop answering">
                <Square className="size-4" aria-hidden /> Stop
              </Button>
            ) : (
              <Button type="submit" variant="primary" disabled={!input.trim()} aria-label="Send">
                <Send className="size-4" aria-hidden />
              </Button>
            )}
          </div>
          <p className="mx-auto mt-2 max-w-3xl text-center text-xs text-ink-2">Enter to send · Shift+Enter for a new line</p>
        </form>
      </section>

      <Sheet open={source !== null} onClose={() => setSource(null)} title={source ? `Source ${source.id.replace("S", "")}` : "Source"}>
        {source && (
          <article>
            <h3 className="text-center text-lg font-semibold text-balance">{source.title}</h3>
            <Crumbs path={source.section_path} className="mt-2 justify-center text-center" />
            <p className="mt-3 flex flex-wrap justify-center gap-1.5">
              <Badge icon={Library}>{source.source}</Badge>
              <Badge icon={FileText}>{source.doc_id}</Badge>
            </p>
            <p className="mt-5 mb-1.5 text-xs font-medium text-ink-2">Passage used</p>
            <blockquote className="rounded-xl bg-info-soft p-3.5 text-sm leading-relaxed whitespace-pre-wrap">
              <RichText text={source.snippet} />
            </blockquote>
          </article>
        )}
      </Sheet>
    </div>
  );
}
