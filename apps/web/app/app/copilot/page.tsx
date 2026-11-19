"use client";

import { Bot, Loader2, Send, Wrench } from "lucide-react";
import Link from "next/link";
import { useRef, useState } from "react";
import { Button, Card, ErrorState, PageHeader } from "@/components/ui";
import { ApiError } from "@/lib/api";
import { useFactory } from "@/lib/hooks";
import { getSession } from "@/lib/session";
import type { CopilotAnswer } from "@/lib/types";

const SUGGESTED = {
  en: ["Why did emissions change in July 2026?", "How much did the boiler retrofit save?", "Which months were flagged for checking?", "What does IFRS S2 require for Scope 2?"],
  bn: ["জুলাই ২০২৬-এ নির্গমন কেন বেড়েছে?", "বয়লার সংস্কারে কত সাশ্রয় হয়েছে?", "কোন মাসগুলো যাচাইয়ের জন্য চিহ্নিত?", "মোট নির্গমন কত?"],
};
type Turn = { q: string; tools: string[]; answer?: CopilotAnswer; error?: string };

async function* stream(factoryId: string, message: string, language: "en" | "bn"): AsyncGenerator<{ type: string; tool?: string; answer?: CopilotAnswer }> {
  const s = getSession();
  const res = await fetch(`/api/v1/factories/${factoryId}/copilot/chat`, {
    method: "POST", headers: { "Content-Type": "application/json", ...(s && { Authorization: `Bearer ${s.token}` }), ...(s?.orgId && { "X-C-Trace-Org": s.orgId }) },
    body: JSON.stringify({ message, language }),
  });
  if (!res.ok || !res.body) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail ?? detail; } catch { /* not json */ }
    throw new ApiError(res.status, detail);
  }
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const block = buf.slice(0, i);
      buf = buf.slice(i + 2);
      const data = block.split("\n").find((l) => l.startsWith("data: "));
      if (data) yield JSON.parse(data.slice(6));
    }
  }
}

function Citation({ c }: { c: CopilotAnswer["citations"][number] }) {
  const cls = "inline-flex items-center rounded border border-teal/30 bg-teal-light px-1.5 py-0.5 font-mono text-[11px] text-teal-dark hover:bg-teal/10";
  if (c.type === "trace") return <Link className={cls} href={`/app/trace?period=${c.id.slice(3, 10)}&trace=${encodeURIComponent(c.id)}`}>{c.id}</Link>;
  if (c.type === "reduction") return <Link className={cls} href="/app/reductions">reduction: {c.label}</Link>;
  if (c.type === "verification") return <Link className={cls} href="/app/verify">verify: {c.label}</Link>;
  if (c.type === "kb") return <span className={cls} title={c.id}>guidance: {c.label}</span>;
  return <Link className={cls} href="/app/trace">{c.label}</Link>;
}

export default function CopilotPage() {
  const { id } = useFactory();
  const [lang, setLang] = useState<"en" | "bn">("en");
  const [q, setQ] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);

  async function ask(question: string) {
    if (!id || !question.trim() || busy) return;
    setBusy(true);
    setQ("");
    const idx = turns.length;
    setTurns((t) => [...t, { q: question, tools: [] }]);
    try {
      for await (const e of stream(id, question, lang)) {
        setTurns((t) => t.map((x, i) => (i !== idx ? x : e.type === "tool" ? { ...x, tools: [...x.tools, e.tool ?? ""] } : { ...x, answer: e.answer })));
        endRef.current?.scrollIntoView({ behavior: "smooth" });
      }
    } catch (err) {
      setTurns((t) => t.map((x, i) => (i === idx ? { ...x, error: err instanceof Error ? err.message : "Failed" } : x)));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-[70vh] flex-col">
      <PageHeader title="C-Trace Copilot" description="Ask about this factory's emissions, flags and savings, or how to report them. Answers use only C-Trace's verified data and cite the trace IDs or guidance they rely on."
        action={<div className="flex rounded-md border border-line p-0.5 text-sm" role="group" aria-label="Answer language">
          {(["en", "bn"] as const).map((l) => <button key={l} type="button" aria-pressed={lang === l} onClick={() => setLang(l)} className={`rounded px-3 py-1 ${lang === l ? "bg-teal text-white" : ""}`}>{l === "en" ? "English" : "বাংলা"}</button>)}
        </div>} />
      <div className="flex-1 space-y-4" aria-live="polite">
        {turns.length === 0 && (
          <Card>
            <p className="mb-3 text-sm text-slate">Try one of these:</p>
            <div className="flex flex-wrap gap-2">{SUGGESTED[lang].map((s) => <Button key={s} variant="secondary" size="sm" onClick={() => ask(s)}>{s}</Button>)}</div>
          </Card>
        )}
        {turns.map((t, i) => (
          <div key={i} className="space-y-2">
            <p className="ml-auto max-w-2xl rounded-2xl rounded-br-sm bg-teal px-4 py-2 text-sm text-white">{t.q}</p>
            <Card className="max-w-3xl">
              <div className="flex gap-3">
                <Bot className="mt-0.5 size-5 shrink-0 text-teal" aria-hidden />
                <div className="min-w-0 flex-1">
                  {t.tools.length > 0 && <p className="mb-2 flex flex-wrap items-center gap-1 text-xs text-slate"><Wrench className="size-3" aria-hidden /> {t.tools.join(" → ")}</p>}
                  {t.error ? <ErrorState error={new Error(t.error)} /> : !t.answer ? <p className="flex items-center gap-2 text-sm text-slate"><Loader2 className="size-4 animate-spin" aria-hidden /> Looking it up…</p> : (
                    <>
                      <p className="text-sm leading-relaxed" lang={t.answer.language}>{t.answer.text}</p>
                      {t.answer.citations.length > 0 && <div className="mt-2 flex flex-wrap gap-1.5">{t.answer.citations.map((c) => <Citation key={c.type + c.id} c={c} />)}</div>}
                      <p className="mt-2 text-[11px] text-slate">{t.answer.method === "offline" ? "Offline mode: answer built from C-Trace data with templates. Add an Anthropic API key for free-form questions." : t.answer.method}</p>
                    </>
                  )}
                </div>
              </div>
            </Card>
          </div>
        ))}
        <div ref={endRef} />
      </div>
      <form className="sticky bottom-0 mt-4 flex gap-2 border-t border-line bg-white py-3" onSubmit={(e) => { e.preventDefault(); ask(q); }}>
        <label htmlFor="copilot-q" className="sr-only">Your question</label>
        <input id="copilot-q" className="h-11 flex-1 rounded-md border border-line px-3 text-sm focus:border-teal focus:outline-none" value={q} onChange={(e) => setQ(e.target.value)}
          placeholder={lang === "en" ? "Ask about emissions, flags or savings…" : "নির্গমন, যাচাই বা সাশ্রয় নিয়ে প্রশ্ন করুন…"} maxLength={1000} lang={lang} />
        <Button type="submit" busy={busy} aria-label="Ask"><Send className="size-4" aria-hidden /></Button>
      </form>
    </div>
  );
}
