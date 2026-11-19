"use client";

import Image from "next/image";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Button, Card, ErrorState, Field, inputCls } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { setSession } from "@/lib/session";
import { supabase } from "@/lib/supabase";

export default function LoginPage() {
  const sb = supabase();
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [step, setStep] = useState<"email" | "code">("email");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function sendCode() {
    if (!sb) return;
    setBusy(true); setError(null);
    const { error } = await sb.auth.signInWithOtp({ email, options: { shouldCreateUser: true } });
    setBusy(false);
    if (error) setError(error); else setStep("code");
  }
  async function verify() {
    if (!sb) return;
    setBusy(true); setError(null);
    const { data, error } = await sb.auth.verifyOtp({ email, token: code, type: "email" });
    if (error || !data.session) { setBusy(false); setError(error ?? new Error("Sign-in failed")); return; }
    setSession({ token: data.session.access_token, kind: "supabase", expiresAt: (data.session.expires_at ?? 0) * 1000 });
    try {
      await api("/v1/me");
      router.replace("/app/dashboard");
    } catch (e) {
      if (e instanceof ApiError && e.status === 403) router.replace("/onboarding");
      else { setBusy(false); setError(e); }
    }
  }

  return (
    <div className="mx-auto flex min-h-screen max-w-md flex-col justify-center px-4">
      <Link href="/" className="mb-6"><Image src="/brand/c-trace-logo.svg" alt="C-Trace" width={150} height={32} /></Link>
      <Card>
        <h1 className="text-lg font-semibold">Sign in</h1>
        {!sb ? (
          <p className="mt-2 text-sm text-slate">Sign-in is not configured on this deployment yet. Use the <Link href="/" className="text-teal hover:underline">live demo</Link> to try C-Trace with a fictional factory.</p>
        ) : step === "email" ? (
          <form className="mt-4 space-y-3" onSubmit={(e) => { e.preventDefault(); sendCode(); }}>
            <Field label="Work email"><input type="email" required autoComplete="email" className={inputCls} value={email} onChange={(e) => setEmail(e.target.value)} /></Field>
            {error ? <ErrorState error={error} /> : null}
            <Button type="submit" busy={busy} className="w-full">Email me a sign-in code</Button>
          </form>
        ) : (
          <form className="mt-4 space-y-3" onSubmit={(e) => { e.preventDefault(); verify(); }}>
            <p className="text-sm text-slate">We sent a code to {email}.</p>
            <Field label="Code"><input inputMode="numeric" autoComplete="one-time-code" required className={inputCls} value={code} onChange={(e) => setCode(e.target.value.trim())} /></Field>
            {error ? <ErrorState error={error} /> : null}
            <Button type="submit" busy={busy} className="w-full">Sign in</Button>
            <button type="button" className="w-full text-sm text-teal hover:underline" onClick={() => setStep("email")}>Use a different email</button>
          </form>
        )}
      </Card>
    </div>
  );
}
