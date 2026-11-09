"use client";

import type { ReactNode } from "react";
import { EmptyState } from "@/components/ui/states";
import { useSession } from "@/lib/use-session";

// The gateway enforces admin-only on /admin/*; this just avoids rendering a page of 403s.
export default function InsightsLayout({ children }: { children: ReactNode }) {
  const user = useSession()?.user;
  if (user?.role !== "admin") {
    return <EmptyState title="This area is for administrators">Sign in as an admin persona to see Insights.</EmptyState>;
  }
  return <>{children}</>;
}
