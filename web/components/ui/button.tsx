import type { ButtonHTMLAttributes } from "react";

type Variant = "primary" | "secondary" | "ghost" | "danger" | "success";

// success / danger are tinted, not solid: white text on the status greens fails contrast, and a
// soft fill keeps Approve and Reject clearly different without shouting.
const styles: Record<Variant, string> = {
  primary: "bg-btn text-white shadow-sm hover:bg-btn-hover",
  secondary: "border border-line bg-surface text-ink hover:bg-surface-2",
  ghost: "text-ink-2 hover:bg-surface-2 hover:text-ink",
  danger: "border border-critical/40 bg-crit-soft text-danger-text hover:border-critical/70",
  success: "border border-good/40 bg-good-soft text-success-text hover:border-good/70",
};

export function Button({
  variant = "secondary",
  size = "md",
  className = "",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; size?: "sm" | "md" }) {
  const pad = size === "sm" ? "h-8 px-2.5 text-sm" : "h-10 px-4 text-sm";
  return (
    <button
      type="button"
      className={`inline-flex items-center justify-center gap-1.5 rounded-lg font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${pad} ${styles[variant]} ${className}`}
      {...props}
    />
  );
}
