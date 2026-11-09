import { Ban, CheckCircle2, CircleDashed, Clock, FileEdit, PackageCheck, XCircle, type LucideIcon } from "lucide-react";
import { statusLabel } from "@/lib/format";
import { Badge, type Tone } from "./badge";

// Status is state, not identity: waiting = amber (needs someone), approved = green, ordered = blue
// (done and handed off), rejected = red, draft/cancelled = neutral. Always an icon and a text label.
export const STATUS: Record<string, { icon: LucideIcon; tone: Tone }> = {
  draft: { icon: FileEdit, tone: "neutral" },
  pending_manager: { icon: Clock, tone: "warn" },
  pending_finance: { icon: Clock, tone: "warn" },
  pending_admin: { icon: Clock, tone: "warn" },
  approved: { icon: CheckCircle2, tone: "good" },
  ordered: { icon: PackageCheck, tone: "info" },
  rejected: { icon: XCircle, tone: "crit" },
  cancelled: { icon: Ban, tone: "neutral" },
};

export function StatusPill({ status }: { status: string }) {
  const s = STATUS[status] ?? { icon: CircleDashed, tone: "neutral" as Tone };
  return (
    <Badge tone={s.tone} icon={s.icon}>
      {statusLabel(status)}
    </Badge>
  );
}

export function PassChip({ pass, label }: { pass: boolean; label?: string }) {
  return (
    <Badge tone={pass ? "good" : "crit"} icon={pass ? CheckCircle2 : XCircle}>
      {label ?? (pass ? "Pass" : "Fail")}
    </Badge>
  );
}
