"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, Download, Link2, Link2Off } from "lucide-react";
import { useState } from "react";
import { Badge, Button, Card, CardTitle, Empty, ErrorState, Field, Loading, PageHeader, inputCls } from "@/components/ui";
import { api, download, post } from "@/lib/api";
import { ACTIVITY_LABEL, monthLabel, n, pct } from "@/lib/format";
import { qk, useFactory, useMe } from "@/lib/hooks";
import type { Report, Summary } from "@/lib/types";

const FORMATS = [
  { id: "buyer", name: "Buyer / brand", hint: "Supplier carbon disclosure for brand questionnaires and Scope 3 inventories" },
  { id: "ifrs_s2", name: "Lender (IFRS S2-aligned)", hint: "Gross Scope 1 and location-based Scope 2, method and data quality, for banks" },
  { id: "bsec", name: "BSEC annual report", hint: "Environmental section: energy by source, emissions, reduction initiatives" },
] as const;

export default function ReportsPage() {
  const { id } = useFactory();
  const me = useMe();
  const qc = useQueryClient();
  const months = useQuery({ queryKey: qk.summary(id ?? "", "", ""), queryFn: () => api<Summary>(`/v1/factories/${id}/summary`), enabled: !!id });
  const list = useQuery({ queryKey: ["reports", id], queryFn: () => api<Report[]>(`/v1/factories/${id}/reports`), enabled: !!id });
  const periods = months.data?.months.map((m) => m.period) ?? [];
  const [fmt, setFmt] = useState<Report["format"]>("buyer");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [openId, setOpenId] = useState<string | null>(null);
  const create = useMutation({
    mutationFn: () => post<Report>(`/v1/factories/${id}/reports`, { format: fmt, period_from: from || periods[0], period_to: to || periods.at(-1) }),
    onSuccess: (r) => { setOpenId(r.id); qc.invalidateQueries({ queryKey: ["reports", id] }); },
  });
  const canCreate = ["owner", "manager", "reviewer"].includes(me.data?.role ?? "");
  const open = list.data?.find((r) => r.id === openId) ?? list.data?.[0];

  return (
    <div>
      <PageHeader title="C-Trace Reports" description="One verified dataset, three audiences. Every figure comes from the trace ledger; narratives written by a model are rejected if they contain any number that isn't in the verified data." />
      {canCreate && (
        <Card>
          <CardTitle>New report</CardTitle>
          <div className="grid gap-3 md:grid-cols-3" role="radiogroup" aria-label="Report format">
            {FORMATS.map((f) => (
              <button key={f.id} type="button" role="radio" aria-checked={fmt === f.id} onClick={() => setFmt(f.id)}
                className={`rounded-lg border p-3 text-left ${fmt === f.id ? "border-teal bg-teal-light" : "border-line hover:bg-slate-50"}`}>
                <span className="block font-medium">{f.name}</span><span className="mt-1 block text-xs text-slate">{f.hint}</span>
              </button>
            ))}
          </div>
          <div className="mt-3 flex flex-wrap items-end gap-3">
            <Field label="From"><select className={`${inputCls} w-40`} value={from || periods[0] || ""} onChange={(e) => setFrom(e.target.value)}>{periods.map((p) => <option key={p} value={p}>{monthLabel(p)}</option>)}</select></Field>
            <Field label="To"><select className={`${inputCls} w-40`} value={to || periods.at(-1) || ""} onChange={(e) => setTo(e.target.value)}>{periods.map((p) => <option key={p} value={p}>{monthLabel(p)}</option>)}</select></Field>
            <Button onClick={() => create.mutate()} busy={create.isPending} disabled={!periods.length}>Generate report</Button>
          </div>
          {create.error && <div className="mt-3"><ErrorState error={create.error} /></div>}
        </Card>
      )}
      <div className="mt-4 grid gap-4 lg:grid-cols-[280px_minmax(0,1fr)]">
        <Card className="p-2 sm:p-2">
          {list.isLoading ? <Loading /> : !list.data?.length ? <p className="p-3 text-sm text-slate">No reports yet.</p> : (
            <ul aria-label="Reports">{list.data.map((r) => (
              <li key={r.id}><button type="button" onClick={() => setOpenId(r.id)} aria-current={open?.id === r.id}
                className={`w-full rounded-md px-3 py-2 text-left text-sm ${open?.id === r.id ? "bg-teal-light" : "hover:bg-slate-50"}`}>
                <span className="block font-medium">{FORMATS.find((f) => f.id === r.format)?.name}</span>
                <span className="text-xs text-slate">{r.period_from} – {r.period_to} · {new Date(r.created_at).toLocaleDateString()}</span>
                {r.shared && <span className="ml-2"><Badge tone="teal">shared</Badge></span>}
              </button></li>
            ))}</ul>
          )}
        </Card>
        {open ? <ReportPreview r={open} canShare={["owner", "manager"].includes(me.data?.role ?? "")} /> : <Empty title="Generate a report to preview it" />}
      </div>
    </div>
  );
}

function ReportPreview({ r, canShare }: { r: Report; canShare: boolean }) {
  const f = r.facts;
  const [busy, setBusy] = useState<string | null>(null);
  const dl = async (ext: "pdf" | "json" | "csv") => { setBusy(ext); try { await download(`/v1/reports/${r.id}/download.${ext}`, `C-Trace_report.${ext}`); } finally { setBusy(null); } };
  return (
    <Card>
      <CardTitle hint={`${f.factory} · ${f.period_from} to ${f.period_to} · narrative: ${r.method}`}
        action={<div className="flex flex-wrap gap-2">{(["pdf", "json", "csv"] as const).map((x) => <Button key={x} size="sm" variant="secondary" busy={busy === x} onClick={() => dl(x)}><Download className="size-3.5" aria-hidden /> {x.toUpperCase()}</Button>)}</div>}>
        {r.title}
      </CardTitle>
      <p className="text-sm leading-relaxed">{r.narrative}</p>
      <div className="tabular mt-4 grid gap-3 sm:grid-cols-4">
        <Metric label="Total Scope 1 + 2" value={`${n(f.total_tco2e, 1)} t`} />
        <Metric label="Scope 1" value={`${n(f.scope1_tco2e, 1)} t`} />
        <Metric label="Scope 2 (location)" value={`${n(f.scope2_location_tco2e, 1)} t`} sub={f.scope2_statutory_tco2e !== undefined ? `Statutory ${n(f.scope2_statutory_tco2e, 1)} t` : undefined} />
        <Metric label="Intensity" value={f.kgco2e_per_piece ? `${f.kgco2e_per_piece} kg/pc` : "—"} />
      </div>
      <div className="mt-4 grid gap-4 md:grid-cols-2">
        <div>
          <h3 className="mb-1 text-sm font-medium">Energy by source</h3>
          <table className="tabular w-full text-sm"><tbody>{Object.entries(f.energy_by_source).map(([a, v]) => (
            <tr key={a} className="border-t border-line"><td className="py-1">{ACTIVITY_LABEL[a]}</td><td className="py-1 text-right">{n(v.quantity)} {v.unit}</td><td className="py-1 text-right">{n(v.tco2e, 1)} t</td></tr>
          ))}</tbody></table>
        </div>
        <div>
          <h3 className="mb-1 text-sm font-medium">Data quality</h3>
          <p className="text-sm text-slate">{f.documents} source documents · {f.data_quality?.human_reviewed_share !== null && f.data_quality?.human_reviewed_share !== undefined ? `${pct(f.data_quality.human_reviewed_share, 0)} human-reviewed` : "review status unknown"}
            {f.confidence_months && <> · months graded {Object.entries(f.confidence_months).map(([k, v]) => `${v} ${k}`).join(", ")}</>}</p>
          {f.verified_reductions?.length ? <p className="mt-2 text-sm">Verified reductions: {f.verified_reductions.map((x) => `${ACTIVITY_LABEL[x.activity]} ${n(x.avoided_tco2e, 1)} t (${n(x.low, 1)}–${n(x.high, 1)})`).join("; ")}</p> : null}
        </div>
      </div>
      <SharePanel r={r} canShare={canShare} />
    </Card>
  );
}

function Metric({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return <div className="rounded-lg bg-teal-light/60 p-3"><p className="text-xs text-slate">{label}</p><p className="font-semibold">{value}</p>{sub && <p className="text-xs text-slate">{sub}</p>}</div>;
}

function SharePanel({ r, canShare }: { r: Report; canShare: boolean }) {
  const qc = useQueryClient();
  const [days, setDays] = useState(14);
  const [docs, setDocs] = useState(false);
  const [link, setLink] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const share = useMutation({ mutationFn: () => post<{ path: string; expires_at: string }>(`/v1/reports/${r.id}/share`, { days, include_documents: docs }),
    onSuccess: (s) => { setLink(`${window.location.origin}${s.path}`); qc.invalidateQueries({ queryKey: ["reports"] }); } });
  const revoke = useMutation({ mutationFn: () => post(`/v1/reports/${r.id}/share/revoke`), onSuccess: () => { setLink(null); qc.invalidateQueries({ queryKey: ["reports"] }); } });
  const views = useQuery({ queryKey: ["share-views", r.id], queryFn: () => api<{ viewed_at: string; what: string; client_hash: string }[]>(`/v1/reports/${r.id}/share/views`), enabled: canShare && (r.shared || !!link) });
  if (!canShare) return null;
  return (
    <div className="mt-5 border-t border-line pt-4">
      <h3 className="text-sm font-medium">Share with a buyer or bank</h3>
      <p className="mt-1 text-xs text-slate">A read-only link to this report and its data-quality summary. It expires, you can revoke it, and every view is logged. Original bills are not visible unless you allow it.</p>
      <div className="mt-3 flex flex-wrap items-end gap-3">
        <Field label="Expires after"><select className={`${inputCls} w-32`} value={days} onChange={(e) => setDays(Number(e.target.value))}>{[1, 7, 14, 30, 90].map((d) => <option key={d} value={d}>{d} day{d > 1 ? "s" : ""}</option>)}</select></Field>
        <label className="flex h-10 items-center gap-2 text-sm"><input type="checkbox" checked={docs} onChange={(e) => setDocs(e.target.checked)} /> Allow opening original bills</label>
        <Button size="sm" onClick={() => share.mutate()} busy={share.isPending}><Link2 className="size-4" aria-hidden /> {r.shared ? "Replace link" : "Create link"}</Button>
        {r.shared && <Button size="sm" variant="danger" onClick={() => revoke.mutate()} busy={revoke.isPending}><Link2Off className="size-4" aria-hidden /> Revoke</Button>}
      </div>
      {link && (
        <div className="mt-3 flex flex-wrap items-center gap-2 rounded-md bg-teal-light p-2">
          <code className="min-w-0 flex-1 truncate font-mono text-xs">{link}</code>
          <Button size="sm" variant="secondary" onClick={async () => { await navigator.clipboard.writeText(link); setCopied(true); setTimeout(() => setCopied(false), 1500); }}><Copy className="size-3.5" aria-hidden /> {copied ? "Copied" : "Copy"}</Button>
          <a className="text-sm text-teal hover:underline" href={link} target="_blank" rel="noreferrer">Open</a>
        </div>
      )}
      {r.shared && !link && <p className="mt-2 text-xs text-slate">A link is active until {r.expires_at ? new Date(r.expires_at).toLocaleString() : "—"}. For security the link itself is shown only once; replace it to get a new one.</p>}
      {(share.error || revoke.error) && <div className="mt-2"><ErrorState error={share.error ?? revoke.error} /></div>}
      {views.data && views.data.length > 0 && (
        <div className="mt-3 text-xs text-slate"><p className="font-medium text-ink">Views ({views.data.length})</p>
          <ul className="mt-1 space-y-0.5">{views.data.slice(0, 8).map((v, i) => <li key={i} className="font-mono">{new Date(v.viewed_at).toLocaleString()} · {v.what} · client {v.client_hash}</li>)}</ul>
        </div>
      )}
    </div>
  );
}
