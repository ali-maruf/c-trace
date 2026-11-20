"use client";

import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, ArrowRight, Search } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useRef, useState } from "react";
import { DocumentViewer, EvidencePanel } from "@/components/document-viewer";
import { Badge, ButtonLink, Card, CardTitle, Empty, ErrorState, Loading, PageHeader, Stat, inputCls } from "@/components/ui";
import { api } from "@/lib/api";
import { ACTIVITY_LABEL, monthLabel, n } from "@/lib/format";
import { qk, useFactory } from "@/lib/hooks";
import type { ActivityRecord, LedgerRow } from "@/lib/types";

type Counted = "counted" | "review" | "excluded";

interface BillRow extends ActivityRecord {
  tco2e: number | null;
  trace_ids: string[];
  counted: Counted;
  readBy: string;
}

const COUNTED_LABEL: Record<Counted, string> = { counted: "Counted", review: "Not counted yet", excluded: "Rejected" };
const STATUS_LABEL: Record<string, string> = { extracted: "not confirmed", confirmed: "confirmed", corrected: "corrected", rejected: "rejected" };

function readBy(method: string | null): string {
  if (!method) return "—";
  return method === "llm" ? "AI model" : method === "rules" ? "Offline parser" : method;
}

function BillsInner() {
  const { id } = useFactory();
  const params = useSearchParams();
  const router = useRouter();
  const [query, setQuery] = useState("");
  const activity = params.get("activity") ?? "";
  const show = (params.get("show") ?? "") as Counted | "";
  const selected = params.get("doc");

  const records = useQuery({ queryKey: qk.records(id ?? ""), queryFn: () => api<ActivityRecord[]>(`/v1/factories/${id}/activity-records`), enabled: !!id });
  const ledger = useQuery({ queryKey: qk.ledger(id ?? "", "", ""), queryFn: () => api<LedgerRow[]>(`/v1/factories/${id}/ledger`), enabled: !!id });

  const rows: BillRow[] = useMemo(() => {
    const byDoc = new Map<string, LedgerRow[]>();
    for (const l of ledger.data ?? []) byDoc.set(l.document_id, [...(byDoc.get(l.document_id) ?? []), l]);
    return (records.data ?? []).map((r) => {
      const lines = byDoc.get(r.document_id) ?? [];
      const counted: Counted = r.status === "rejected" ? "excluded" : lines.length ? "counted" : "review";
      return { ...r, tco2e: lines.length ? lines.reduce((s, l) => s + l.tco2e, 0) : null, trace_ids: lines.map((l) => l.trace_id),
               counted, readBy: readBy(r.extraction_method) };
    }).sort((a, b) => (a.period ?? "9999").localeCompare(b.period ?? "9999") || (a.activity ?? "").localeCompare(b.activity ?? ""));
  }, [records.data, ledger.data]);

  const visible = rows.filter((r) => (!activity || r.activity === activity) && (!show || r.counted === show)
    && (!query || `${r.doc_name} ${r.supplier ?? ""} ${r.bill_no ?? ""} ${r.period ?? ""}`.toLowerCase().includes(query.toLowerCase())));
  const counted = rows.filter((r) => r.counted === "counted");
  const flagged = rows.filter((r) => r.issues?.length);
  const total = counted.reduce((s, r) => s + (r.tco2e ?? 0), 0);
  const row = rows.find((r) => r.document_id === selected) ?? null;
  const detail = useRef<HTMLDivElement>(null);
  useEffect(() => {  // below xl the detail sits under the table: bring it into view
    if (row && detail.current && window.innerWidth < 1280) detail.current.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [row?.document_id]);  // eslint-disable-line react-hooks/exhaustive-deps

  const set = (k: string, v: string) => {
    const p = new URLSearchParams(params.toString());
    if (v) p.set(k, v); else p.delete(k);
    router.replace(`/app/bills?${p.toString()}`, { scroll: false });
  };

  const error = records.error ?? ledger.error;
  return (
    <div>
      <PageHeader title="Bills" description="Every bill C-Trace has read for this factory, what it read, and how much CO₂e each one adds to the total."
        action={<ButtonLink href="/app/upload" variant="secondary">Upload bills</ButtonLink>} />
      {error && <ErrorState error={error} retry={() => { records.refetch(); ledger.refetch(); }} />}
      {records.isLoading || ledger.isLoading ? <Loading label="Loading bills" /> : !rows.length ? (
        <Empty title="No bills yet" action={<ButtonLink href="/app/upload">Upload bills</ButtonLink>}>Upload gas, electricity or diesel bills to start the ledger.</Empty>
      ) : (
        <>
          <div className="mb-4 grid gap-3 sm:grid-cols-4">
            <Stat label="Bills" value={n(rows.length)} sub={`${n(new Set(rows.map((r) => r.period).filter(Boolean)).size)} months`} />
            <Stat label="Counted in the total" value={n(counted.length)} sub={`${n(total, 1)} tCO₂e`} tone="teal" />
            <Stat label="With a warning" value={n(flagged.length)} sub={flagged.length ? "check in the review queue" : "none"} />
            <Stat label="Not counted" value={n(rows.length - counted.length)} sub="month or quantity missing, or rejected" />
          </div>

          <div className="mb-4 flex flex-wrap gap-3">
            <label className="relative w-full sm:w-64">
              <span className="sr-only">Search bills</span>
              <Search className="pointer-events-none absolute left-3 top-3 size-4 text-slate" aria-hidden />
              <input className={`${inputCls} pl-9`} placeholder="File, supplier, bill no., month" value={query} onChange={(e) => setQuery(e.target.value)} />
            </label>
            <select aria-label="Energy source" className={`${inputCls} w-full sm:w-48`} value={activity} onChange={(e) => set("activity", e.target.value)}>
              <option value="">All sources</option>
              {Object.entries(ACTIVITY_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
            <select aria-label="Counted in the total" className={`${inputCls} w-full sm:w-48`} value={show} onChange={(e) => set("show", e.target.value)}>
              <option value="">All bills</option>
              {Object.entries(COUNTED_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
            <p className="tabular self-center text-sm text-slate">{visible.length} of {rows.length} bills</p>
          </div>

          <div className={`grid gap-4 ${row ? "xl:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]" : ""}`}>
            <Card className="overflow-x-auto p-0 sm:p-0">
              {!visible.length ? <div className="p-4"><Empty title="No bills match">Change the search or filters.</Empty></div> : (
                <table className="tabular w-full min-w-[720px] text-sm">
                  <thead className="bg-teal-light text-left text-xs text-slate">
                    <tr>
                      <th className="px-3 py-2">Bill</th><th className="px-3 py-2">Month</th><th className="px-3 py-2">Source</th>
                      <th className="px-3 py-2 text-right">Quantity</th><th className="px-3 py-2 text-right">Amount (BDT)</th>
                      <th className="px-3 py-2 text-right">tCO₂e</th><th className="px-3 py-2">Read by</th><th className="px-3 py-2">In total</th>
                    </tr>
                  </thead>
                  <tbody>
                    {visible.map((r) => (
                      <tr key={r.id} tabIndex={0} aria-selected={selected === r.document_id}
                        className={`cursor-pointer border-t border-line hover:bg-teal-light/60 ${selected === r.document_id ? "bg-teal-light" : ""}`}
                        onClick={() => set("doc", r.document_id)} onKeyDown={(e) => e.key === "Enter" && set("doc", r.document_id)}>
                        <td className="px-3 py-2">
                          <span className="break-all font-mono text-xs">{r.doc_name}</span>
                          {r.issues?.length ? <AlertTriangle className="ml-1 inline size-3.5 text-medium" aria-label="Has a warning" /> : null}
                        </td>
                        <td className="whitespace-nowrap px-3 py-2">{r.period ?? "—"}</td>
                        <td className="px-3 py-2">{r.activity ? ACTIVITY_LABEL[r.activity] : "—"}</td>
                        <td className="whitespace-nowrap px-3 py-2 text-right">{r.quantity === null ? "—" : `${n(r.quantity)} ${r.unit ?? ""}`}</td>
                        <td className="px-3 py-2 text-right">{r.amount_bdt === null ? "—" : n(r.amount_bdt)}</td>
                        <td className="px-3 py-2 text-right font-medium">{r.tco2e === null ? "—" : n(r.tco2e, 2)}</td>
                        <td className="whitespace-nowrap px-3 py-2 text-xs text-slate">{r.readBy}</td>
                        <td className="whitespace-nowrap px-3 py-2" title={`Review: ${STATUS_LABEL[r.status] ?? r.status}`}>
                          <Badge tone={r.counted === "counted" ? "teal" : r.counted === "review" ? "medium" : "low"}>{COUNTED_LABEL[r.counted]}</Badge>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </Card>

            {row && (
              <div ref={detail} className="scroll-mt-4 space-y-4">
                <Card>
                  <CardTitle hint={row.bill_no ?? undefined}>
                    {row.activity ? ACTIVITY_LABEL[row.activity] : "Unknown bill type"} · {row.period ? monthLabel(row.period) : "month not read"}
                  </CardTitle>
                  <dl className="grid gap-2 text-sm sm:grid-cols-2">
                    <div><dt className="text-slate">Supplier</dt><dd>{row.supplier ?? "—"}</dd></div>
                    <div><dt className="text-slate">Adds to the total</dt><dd className="tabular font-medium">{row.tco2e === null ? "Nothing yet" : `${n(row.tco2e, 2)} tCO₂e`}</dd></div>
                    <div><dt className="text-slate">Review status</dt><dd>{STATUS_LABEL[row.status] ?? row.status}{row.confirmed_at ? ` · ${new Date(row.confirmed_at).toLocaleDateString()}` : ""}</dd></div>
                    <div><dt className="text-slate">Read by</dt><dd>{row.readBy}{row.extraction_confidence !== null ? ` · ${Math.round(row.extraction_confidence * 100)}% confidence` : ""}</dd></div>
                  </dl>
                  {row.issues?.length ? (
                    <ul className="mt-3 space-y-1 rounded-md bg-medium/10 p-3 text-sm">
                      {row.issues.map((i) => <li key={i} className="flex gap-2"><AlertTriangle className="mt-0.5 size-4 shrink-0 text-medium" aria-hidden />{i}</li>)}
                    </ul>
                  ) : null}
                  <div className="mt-3 flex flex-wrap gap-3 text-sm">
                    {row.trace_ids.map((tid) => (
                      <Link key={tid} href={`/app/trace?trace=${encodeURIComponent(tid)}`} className="inline-flex items-center gap-1 font-mono text-xs text-teal hover:underline">
                        {tid} <ArrowRight className="size-3" aria-hidden />
                      </Link>
                    ))}
                    {row.status === "extracted" && <Link href="/app/review" className="text-teal hover:underline">Review this bill</Link>}
                  </div>
                </Card>
                <Card><CardTitle>What C-Trace read</CardTitle><EvidencePanel documentId={row.document_id} /></Card>
                <Card><CardTitle>Original bill</CardTitle><DocumentViewer documentId={row.document_id} /></Card>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}

export default function BillsPage() {
  return <Suspense fallback={<Loading />}><BillsInner /></Suspense>;
}
