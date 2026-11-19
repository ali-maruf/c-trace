"use client";

import { BarChart3, ClipboardCheck, FileSearch, FileText, Gauge, LogOut, Menu, MessageSquare, Settings, ShieldCheck, TrendingDown, Upload, X } from "lucide-react";
import Image from "next/image";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ApiError } from "@/lib/api";
import { useMe, useSession } from "@/lib/hooks";
import { clearSession } from "@/lib/session";
import { cn } from "@/lib/cn";
import { Loading } from "./ui";

const NAV = [
  { href: "/app/dashboard", label: "Dashboard", icon: BarChart3 },
  { href: "/app/upload", label: "Upload bills", icon: Upload },
  { href: "/app/review", label: "Review queue", icon: ClipboardCheck },
  { href: "/app/trace", label: "Trace ledger", icon: FileSearch },
  { href: "/app/verify", label: "Verify", icon: ShieldCheck },
  { href: "/app/reductions", label: "Reductions", icon: TrendingDown },
  { href: "/app/reports", label: "Reports", icon: FileText },
  { href: "/app/copilot", label: "Copilot", icon: MessageSquare },
  { href: "/app/settings", label: "Settings", icon: Settings },
];

function Nav({ onNavigate }: { onNavigate?: () => void }) {
  const path = usePathname();
  return (
    <nav aria-label="Main" className="flex flex-col gap-0.5">
      {NAV.map(({ href, label, icon: Icon }) => {
        const active = path === href || path.startsWith(`${href}/`);
        return (
          <Link key={href} href={href} onClick={onNavigate} aria-current={active ? "page" : undefined}
            className={cn("flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium", active ? "bg-teal text-white" : "text-ink hover:bg-white")}>
            <Icon className="size-4" aria-hidden /> {label}
          </Link>
        );
      })}
    </nav>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const session = useSession();
  const me = useMe();
  const router = useRouter();
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (session === null) router.replace("/?signin=1");
  }, [session, router]);
  useEffect(() => {
    if (me.error instanceof ApiError && me.error.status === 403 && session?.kind === "supabase") router.replace("/onboarding");
  }, [me.error, session, router]);

  if (!session) return <div className="p-8"><Loading label="Checking your session" /></div>;
  const membership = me.data?.memberships.find((m) => m.org_id === me.data?.org_id);
  const hoursLeft = Math.max(0, Math.round((session.expiresAt - Date.now()) / 3_600_000));

  const sidebar = (
    <div className="flex h-full flex-col gap-6 bg-teal-light p-4">
      <Link href="/" className="flex items-center" aria-label="C-Trace home">
        <Image src="/brand/c-trace-logo.svg" alt="C-Trace" width={140} height={30} priority />
      </Link>
      <Nav onNavigate={() => setOpen(false)} />
      <div className="mt-auto space-y-3 text-xs text-slate">
        {membership && (
          <p>
            <span className="block font-medium text-ink">{membership.org_name}</span>
            Role: {membership.role}
          </p>
        )}
        <Link href="/model-card" className="flex items-center gap-2 hover:text-teal"><Gauge className="size-3.5" aria-hidden /> Model card</Link>
        <button type="button" onClick={() => { clearSession(); router.replace("/"); }} className="flex items-center gap-2 hover:text-teal">
          <LogOut className="size-3.5" aria-hidden /> {session.kind === "demo" ? "Leave demo" : "Sign out"}
        </button>
      </div>
    </div>
  );

  return (
    <div className="min-h-screen lg:grid lg:grid-cols-[240px_1fr]">
      <aside className="sticky top-0 hidden h-screen lg:block">{sidebar}</aside>
      <div className="flex items-center justify-between border-b border-line px-4 py-3 lg:hidden">
        <Image src="/brand/c-trace-logo.svg" alt="C-Trace" width={120} height={26} />
        <button type="button" onClick={() => setOpen(true)} aria-label="Open menu" className="rounded-md p-2 hover:bg-teal-light"><Menu className="size-5" /></button>
      </div>
      {open && (
        <div className="fixed inset-0 z-50 lg:hidden" role="dialog" aria-modal="true" aria-label="Menu">
          <div className="absolute inset-0 bg-ink/40" onClick={() => setOpen(false)} />
          <div className="absolute inset-y-0 left-0 w-72">
            <button type="button" onClick={() => setOpen(false)} aria-label="Close menu" className="absolute right-3 top-3 z-10 rounded-md p-1 hover:bg-white"><X className="size-5" /></button>
            {sidebar}
          </div>
        </div>
      )}
      <main id="main" className="min-w-0">
        {session.kind === "demo" && (
          <div className="border-b border-medium/30 bg-medium-bg px-4 py-2 text-center text-xs text-medium sm:text-sm">
            Live demo sandbox — every factory, supplier and bill here is <strong>fictional</strong>. Private to this browser tab; deleted in about {hoursLeft} h.
          </div>
        )}
        <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8">{children}</div>
      </main>
    </div>
  );
}
