"use client";

import { GATEWAY, post } from "./api";
import type { ActionOutcome } from "./conversations";
import { loadSession } from "./session";
import { streamSse } from "./sse";
import type { Proposal } from "./types";

/** Confirm a proposal: the server executes it once and streams the result. */
export async function confirmAction(p: Proposal): Promise<ActionOutcome> {
  let text = "";
  let status = "unavailable";
  let result: Record<string, unknown> | null = null;
  try {
    await streamSse(
      `${GATEWAY}/v1/actions/${encodeURIComponent(p.proposal_id)}/confirm`,
      { token: loadSession()?.token },
      ({ event, data }) => {
        const d = data as Record<string, unknown>;
        if (event === "delta") text += String(d.text ?? "");
        if (event === "done") {
          status = String(d.status ?? status);
          result = (d.result as Record<string, unknown> | null) ?? null;
          if (!text && typeof d.message === "string") text = d.message;
        }
      },
    );
  } catch {
    text = "Couldn’t reach the service. Nothing was changed.";
  }
  const r = result as Record<string, unknown> | null;
  const ok = status === "executed";
  return {
    status,
    message: text || (ok ? "Done." : "This action could not be completed. Nothing was changed."),
    prId: ok ? ((r?.pr_id as string | undefined) ?? (p.summary.pr_id as string | undefined)) : undefined,
  };
}

export async function cancelAction(p: Proposal): Promise<ActionOutcome> {
  try {
    await post(`/v1/actions/${encodeURIComponent(p.proposal_id)}/cancel`);
  } catch {
    /* an expired proposal is already as good as cancelled */
  }
  return { status: "cancelled", message: "Cancelled. Nothing was changed." };
}
