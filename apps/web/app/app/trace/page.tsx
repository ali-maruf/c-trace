"use client";

import { useQuery } from "@tanstack/react-query";
import { ExternalLink } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { DocumentViewer, EvidencePanel } from "@/components/document-viewer";
import { Badge, Card, CardTitle, Empty, ErrorState, Loading, PageHeader, inputCls } from "@/components/ui";
import { api } from "@/lib/api";
import { ACTIVITY_LABEL, monthLabel, n, shortHash } from "@/lib/format";
import { qk, useFactory } from "@/lib/hooks";
import type { LedgerRow, Summary } from "@/lib/types";

function TraceInner() {
  const { id } = useFactory();
  const params = useSearchParams();
  const router = useRouter();
  const period = params.get("period") ?? "";
  const activity = params.get("activity") ?? "";
  const [selected, setSelected] = useState<string | null>(params.get("trace"));
  const months = useQuery({ queryKey: qk.summary(id ?? "", "", ""), queryFn: () => api<Summary>(`/v1/factories/${id}/summary`), enabled: !!id });
  const q = new URLSearchParams({ ...(period && { period }), ...(activity && { activity }) }).toString();
  const ledger = useQuery({ queryKey: qk.ledger(id ?? "", period, activity), queryFn: () => api<LedgerRow[]>(`/v1/factories/${id}/ledger${q ? `?${q}` : ""}`), enabled: !!id });
  const row = ledger.data?.find((r) => r.trace_id === selected) ?? null;
  const set = (k: string, v: string) => {
    const p = new URLSearchParams(params.toString());
    if (v) p.set(k, v); else p.delete(k);
    p.delete("trace");
    setSelected(null);
    router.replace(`/app/trace?${p.toString()}`);
  };
  const total = ledger.data?.reduce((s, r) => s + r.tco2e, 0) ?? 0;

  return (
    <div>
      <PageHeader title="Trace ledger" description="Every tonne C-Trace reports, with the bill it came from, the factor it used and the arithmetic." />
      <div className="mb-4 flex flex-wrap gap-3">
        <select aria-label="Month" className={`${inputCls} w-44`} value={period} onChange={(e) => set("period", e.target.value)}>
          <option value="">All months</option>
          {months.data?.months.map((m) => <option key={m.period} value={m.period}>{monthLabel(m.period)}</option>)}
        </select>
        <select aria-label="Energy source" className={`${inputCls} w-48`} value={activity} onChange={(e) => set("activity", e.target.value)}>
          <option value="">All sources</option>
          {Object.entries(ACTIVITY_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        {ledger.data && <p className="tabular self-center text-sm text-slate">{ledger.data.length} rows · {n(total, 2)} tCO₂e</p>}
      </div>
      {ledger.error && <ErrorState error={ledger.error} retry={() => ledger.refetch()} />}
      <div className={`grid gap-4 ${row ? "xl:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]" : ""}`}>
        <Card className="overflow-x-auto p-0 sm:p-0">
          {ledger.isLoading ? <div className="p-4"><Loading /></div> : !ledger.data?.length ? <div className="p-4"><Empty title="No ledger rows">Nothing matches this filter.</Empty></div> : (
            <table className="tabular w-full min-w-[640px] text-sm">
              <thead className="bg-teal-light text-left text-xs text-slate">
                <tr><th className="px-3 py-2">Trace ID</th><th className="px-3 py-2">Month</th><th className="px-3 py-2">Source</th><th className="px-3 py-2 text-right">Quantity</th><th className="px-3 py-2 text-right">tCO₂e</th><th className="px-3 py-2">Status</th></tr>
              </thead>
              <tbody>
                {ledger.data.map((r) => (
                  <tr key={r.trace_id} className={`cursor-pointer border-t border-line hover:bg-teal-light/60 ${selected === r.trace_id ? "bg-teal-light" : ""}`}
                    onClick={() => setSelected(r.trace_id)} onKeyDown={(e) => e.key === "Enter" && setSelected(r.trace_id)} tabIndex={0} aria-selected={selected === r.trace_id}>
                    <td className="px-3 py-2 font-mono text-xs">{r.trace_id}</td>
                    <td className="px-3 py-2">{r.period}</td>
                    <td className="px-3 py-2">{ACTIVITY_LABEL[r.activity]}</td>
                    <td className="px-3 py-2 text-right">{n(r.quantity)} {r.unit}</td>
                    <td className="px-3 py-2 text-right font-medium">{n(r.tco2e, 2)}</td>
                    <td className="px-3 py-2"><Badge tone={r.record_status === "extracted" ? "slate" : "teal"}>{r.record_status}</Badge></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
        {row && (
          <div className="space-y-4">
            <Card>
              <CardTitle hint={<span className="font-mono">{row.trace_id}</span>}>{ACTIVITY_LABEL[row.activity]} · {monthLabel(row.period)}</CardTitle>
              <p className="rounded-md bg-ink px-3 py-2 font-mono text-sm text-white">{row.calculation}</p>
              <dl className="mt-3 grid gap-2 text-sm sm:grid-cols-2">
                <div><dt className="text-slate">Emission factor</dt><dd className="font-mono text-xs">{row.factor_id} ({row.factor_version})</dd></div>
                <div><dt className="text-slate">Value</dt><dd className="tabular">{row.kgco2e_per_unit} kgCO₂e/{row.unit} · Scope {row.scope}</dd></div>
                <div className="sm:col-span-2"><dt className="text-slate">Source</dt><dd>{row.factor_source}{" "}
                  <a href={row.factor_source_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-teal hover:underline">publication <ExternalLink className="size-3" aria-hidden /></a></dd></div>
                <div className="sm:col-span-2"><dt className="text-slate">Document</dt><dd className="font-mono text-xs" title={row.doc_sha256}>{row.doc_name} · {shortHash(row.doc_sha256)}</dd></div>
              </dl>
            </Card>
            <Card><CardTitle>What C-Trace read</CardTitle><EvidencePanel documentId={row.document_id} /></Card>
            <Card><CardTitle>Original bill</CardTitle><DocumentViewer documentId={row.document_id} /></Card>
          </div>
        )}
      </div>
    </div>
  );
}

export default function TracePage() {
  return <Suspense fallback={<Loading />}><TraceInner /></Suspense>;
}
