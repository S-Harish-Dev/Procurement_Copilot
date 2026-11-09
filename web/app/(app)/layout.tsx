"use client";

import { useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";
import { Nav } from "@/components/nav";
import { Skeleton } from "@/components/ui/states";
import { useHydrated, useSession } from "@/lib/use-session";

export default function AppLayout({ children }: { children: ReactNode }) {
  const session = useSession();
  const hydrated = useHydrated();
  const router = useRouter();
  useEffect(() => {
    if (hydrated && !session) router.replace("/login");
  }, [hydrated, session, router]);

  if (!session) {
    return (
      <div className="mx-auto max-w-7xl p-6" aria-busy="true">
        <Skeleton className="h-8 w-60" />
      </div>
    );
  }
  return (
    <>
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:z-50 focus:rounded focus:bg-surface focus:px-3 focus:py-2">
        Skip to content
      </a>
      <Nav user={session.user} />
      <main id="main" className="mx-auto max-w-7xl px-4 py-6">
        {children}
      </main>
    </>
  );
}
