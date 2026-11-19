"use client";

import { ArrowRight, Calculator, FileScan, Landmark, Recycle, ShieldCheck } from "lucide-react";
import Image from "next/image";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { Button } from "@/components/ui";
import { post } from "@/lib/api";
import { setSession } from "@/lib/session";

const STEPS = [
  { icon: FileScan, title: "Read the bills", body: "Gas and electricity bills and diesel receipts — PDFs, scans, phone photos, English or Bangla. Every value keeps the exact text it was read from." },
  { icon: Calculator, title: "Calculate, deterministically", body: "Scope 1 and 2 with cited IPCC and Ember factors. Code does the arithmetic, never a language model, and every tonne links to its bill." },
  { icon: ShieldCheck, title: "Verify each month", body: "The C-Trace Verifier predicts what the factory should have used, given production, weather and assets, and asks a human to check months that don't add up." },
  { icon: Recycle, title: "Prove reductions, report once", body: "Savings proven against a weather- and production-adjusted baseline, then buyer, IFRS S2 lender and BSEC reports from one verified dataset." },
];

function DemoButton({ large }: { large?: boolean }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function start() {
    setBusy(true);
    setError(null);
    try {
      const r = await post<{ token: string; expires_in: number; org_id: string; factory_id: string }>("/v1/demo/start");
      setSession({ token: r.token, kind: "demo", expiresAt: Date.now() + r.expires_in * 1000, orgId: r.org_id, factoryId: r.factory_id });
      router.push("/app/dashboard");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not start the demo");
      setBusy(false);
    }
  }
  return (
    <div>
      <Button onClick={start} busy={busy} className={large ? "h-12 px-6 text-base" : undefined}>
        {busy ? "Loading 65 fictional bills…" : "Try the live demo"} {!busy && <ArrowRight className="size-4" aria-hidden />}
      </Button>
      {error && <p role="alert" className="mt-2 text-sm text-low">{error}</p>}
    </div>
  );
}

function SignInNote() {
  const params = useSearchParams();
  return params.get("signin") ? (
    <p className="mt-4 rounded-md bg-teal-light px-3 py-2 text-sm text-teal-dark">Your session ended. Start the demo again or sign in.</p>
  ) : null;
}

export default function Landing() {
  return (
    <div className="min-h-screen">
      <header className="mx-auto flex max-w-6xl items-center justify-between px-4 py-5 sm:px-6">
        <Image src="/brand/c-trace-logo.svg" alt="C-Trace" width={150} height={32} priority />
        <nav className="flex items-center gap-4 text-sm">
          <Link href="/model-card" className="text-slate hover:text-teal">Model card</Link>
          <Link href="/login" className="text-slate hover:text-teal">Sign in</Link>
        </nav>
      </header>

      <section className="mx-auto max-w-6xl px-4 pb-16 pt-8 sm:px-6 sm:pt-16">
        <p className="text-sm font-semibold uppercase tracking-wider text-teal">Verifiable carbon MRV for factories</p>
        <h1 className="mt-3 max-w-3xl text-4xl font-bold tracking-tight text-ink sm:text-5xl">Trace every tonne.</h1>
        <p className="mt-5 max-w-2xl text-lg text-slate">
          C-Trace turns a factory&apos;s paper gas, electricity and diesel bills into verified Scope 1 and 2 emissions, proven reductions and
          reports for buyers, banks and regulators. Every number can be clicked back to the bill it came from.
        </p>
        <div className="mt-8 flex flex-wrap items-center gap-4">
          <DemoButton large />
          <span className="text-sm text-slate">No sign-up. A private sandbox with a fictional knit-composite factory.</span>
        </div>
        <Suspense><SignInNote /></Suspense>
      </section>

      <section className="border-y border-line bg-teal-light/60">
        <div className="mx-auto grid max-w-6xl gap-6 px-4 py-12 sm:px-6 md:grid-cols-3">
          <div>
            <h2 className="font-semibold text-ink">Buyers ask for supplier emissions</h2>
            <p className="mt-2 text-sm text-slate">Brands report their Scope 3 and ask garment suppliers for energy and emissions data they can rely on.</p>
          </div>
          <div>
            <h2 className="font-semibold text-ink">Banks finance transitions</h2>
            <p className="mt-2 text-sm text-slate">Lenders need borrower emissions for IFRS S2-aligned reporting and evidence before they can call a loan green.</p>
          </div>
          <div>
            <h2 className="font-semibold text-ink">The data is on paper</h2>
            <p className="mt-2 text-sm text-slate">Bills arrive as PDFs, scans and photos, often in Bangla. Retyping them into spreadsheets loses the audit trail.</p>
          </div>
        </div>
      </section>

      <section className="mx-auto max-w-6xl px-4 py-16 sm:px-6">
        <h2 className="text-2xl font-semibold tracking-tight">How C-Trace works</h2>
        <ol className="mt-8 grid gap-6 sm:grid-cols-2 lg:grid-cols-4">
          {STEPS.map(({ icon: Icon, title, body }, i) => (
            <li key={title} className="rounded-xl border border-line p-5">
              <div className="flex items-center gap-3">
                <span className="flex size-9 items-center justify-center rounded-full bg-teal text-sm font-semibold text-white">{i + 1}</span>
                <Icon className="size-5 text-teal" aria-hidden />
              </div>
              <h3 className="mt-4 font-semibold">{title}</h3>
              <p className="mt-2 text-sm text-slate">{body}</p>
            </li>
          ))}
        </ol>
      </section>

      <section className="mx-auto max-w-6xl px-4 pb-16 sm:px-6">
        <div className="grid gap-6 rounded-2xl bg-ink p-8 text-white md:grid-cols-[1.4fr_1fr]">
          <div>
            <h2 className="text-xl font-semibold">AI reads and explains. Code calculates.</h2>
            <p className="mt-3 text-sm text-slate-300">
              Language models only read documents and write explanations; any figure in their text must match a verified number or it is
              rejected. Simulated training data is labelled as simulated, and every metric in the model card is computed by code, including
              how often each kind of factory is flagged.
            </p>
          </div>
          <div className="flex flex-col justify-center gap-3 text-sm">
            <Link href="/model-card" className="inline-flex items-center gap-2 font-medium text-teal-light hover:underline"><Landmark className="size-4" aria-hidden /> Read the model card</Link>
            <a href="https://github.com/ali-maruf/c-trace" className="inline-flex items-center gap-2 font-medium text-teal-light hover:underline">Source code and methods</a>
          </div>
        </div>
      </section>

      <footer className="border-t border-line">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 px-4 py-6 text-xs text-slate sm:px-6">
          <span>C-Trace — Trace every tonne.</span>
          <span>Demo data is fictional. Public data: Our World in Data / Ember, NASA POWER, Climate TRACE (CC BY 4.0).</span>
        </div>
      </footer>
    </div>
  );
}
