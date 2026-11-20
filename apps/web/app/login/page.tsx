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
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  // Accounts are created by an administrator (Supabase dashboard → Authentication → Add user); there is no self sign-up.
  async function signIn() {
    if (!sb) return;
    setBusy(true); setError(null);
    const { data, error } = await sb.auth.signInWithPassword({ email, password });
    if (error || !data.session) {
      setBusy(false);
      setError(error?.message === "Invalid login credentials" ? new Error("Wrong email or password.") : (error ?? new Error("Sign-in failed")));
      return;
    }
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
        ) : (
          <form className="mt-4 space-y-3" onSubmit={(e) => { e.preventDefault(); signIn(); }}>
            <Field label="Email"><input type="email" required autoComplete="username" className={inputCls} value={email} onChange={(e) => setEmail(e.target.value.trim())} /></Field>
            <Field label="Password"><input type="password" required autoComplete="current-password" className={inputCls} value={password} onChange={(e) => setPassword(e.target.value)} /></Field>
            {error ? <ErrorState error={error} /> : null}
            <Button type="submit" busy={busy} className="w-full">Sign in</Button>
            <p className="text-xs text-slate">Accounts are set up by your C-Trace administrator. No account? Try the <Link href="/" className="text-teal hover:underline">live demo</Link>.</p>
          </form>
        )}
      </Card>
    </div>
  );
}
