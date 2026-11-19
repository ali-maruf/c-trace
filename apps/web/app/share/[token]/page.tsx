"use client";

import { useQuery } from "@tanstack/react-query";
import { Download, ShieldCheck } from "lucide-react";
import Image from "next/image";
import { use } from "react";
import { Card, CardTitle, ErrorState, Loading } from "@/components/ui";
import { ApiError } from "@/lib/api";
import { ACTIVITY_LABEL, n, pct } from "@/lib/format";
import type { ReportFacts } from "@/lib/types";

type Shared = {
  title: string; format: string; period_from: string; period_to: string; narrative: string; method: string; created_at: string; expires_at: string;
  factory: { name: string; location: string | null; factory_type: string } | null; facts: ReportFacts; include_documents: boolean;
  ledger?: { trace_id: string; period: string; activity: string; quantity: number; unit: string; tco2e: number; document_id: string; file_name: string; doc_sha256: string }[];
};

async function fetchShared(token: string): Promise<Shared> {
  const res = await fetch(`/api/v1/shared/${encodeURIComponent(token)}`);
  if (!res.ok) throw new ApiError(res.status, res.status === 410 ? "This link has expired or was revoked by the factory." : res.status === 404 ? "This link is not valid." : "Could not load the report.");
  return res.json();
}

export default function SharedReport({ params }: { params: Promise<{ token: string }> }) {
  const { token } = use(params);
  const q = useQuery({ queryKey: ["shared", token], queryFn: () => fetchShared(token), retry: false });
  return (
    <div className="mx-auto max-w-5xl px-4 py-8 sm:px-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Image src="/brand/c-trace-logo.svg" alt="C-Trace" width={140} height={30} />
        <span className="inline-flex items-center gap-1.5 rounded-full bg-teal-light px-3 py-1 text-xs text-teal-dark"><ShieldCheck className="size-3.5" aria-hidden /> Shared, read-only report</span>
      </div>
      {q.isLoading && <Loading label="Loading the shared report" />}
      {q.error && <div className="mt-8"><ErrorState error={q.error} /></div>}
      {q.data && <SharedBody d={q.data} token={token} />}
    </div>
  );
}

function SharedBody({ d, token }: { d: Shared; token: string }) {
  const f = d.facts;
  return (
    <>
      <h1 className="mt-6 text-2xl font-semibold tracking-tight">{d.title}</h1>
      <p className="mt-1 text-sm text-slate">{d.factory?.name} · {d.factory?.location} · {d.period_from} to {d.period_to} · link valid until {new Date(d.expires_at).toLocaleDateString()}</p>
      <div className="tabular mt-5 grid gap-3 sm:grid-cols-4">
        {[["Total Scope 1 + 2", `${n(f.total_tco2e, 1)} tCO₂e`], ["Scope 1", `${n(f.scope1_tco2e, 1)} t`], ["Scope 2, location-based", `${n(f.scope2_location_tco2e, 1)} t`],
          ["Intensity", f.kgco2e_per_piece ? `${f.kgco2e_per_piece} kg/piece` : "—"]].map(([k, v]) => (
          <Card key={k}><p className="text-sm text-slate">{k}</p><p className="mt-1 text-xl font-semibold text-teal">{v}</p></Card>
        ))}
      </div>
      <Card className="mt-4">
        <CardTitle hint={`Narrative: ${d.method}`}>Summary</CardTitle>
        <p className="text-sm leading-relaxed">{d.narrative}</p>
        <a href={`/api/v1/shared/${encodeURIComponent(token)}/report.pdf`} className="mt-4 inline-flex h-9 items-center gap-2 rounded-md border border-line px-3 text-sm hover:bg-teal-light"><Download className="size-4" aria-hidden /> Download PDF</a>
      </Card>
      <div className="mt-4 grid gap-4 md:grid-cols-2">
        <Card>
          <CardTitle>Energy by source</CardTitle>
          <table className="tabular w-full text-sm"><tbody>{Object.entries(f.energy_by_source).map(([a, v]) => (
            <tr key={a} className="border-t border-line"><td className="py-1.5">{ACTIVITY_LABEL[a]}</td><td className="py-1.5 text-right">{n(v.quantity)} {v.unit}</td><td className="py-1.5 text-right">{n(v.tco2e, 1)} t</td></tr>
          ))}</tbody></table>
        </Card>
        <Card>
          <CardTitle>Data quality</CardTitle>
          <ul className="space-y-1 text-sm">
            <li>{f.documents} source documents, each identified by its SHA-256 fingerprint</li>
            {f.data_quality?.human_reviewed_share !== null && f.data_quality?.human_reviewed_share !== undefined && <li>{pct(f.data_quality.human_reviewed_share, 0)} of bills confirmed or corrected by a person</li>}
            {f.confidence_months && <li>Months graded by the {f.data_quality?.verification_model ?? "C-Trace Verifier"}: {Object.entries(f.confidence_months).map(([k, v]) => `${v} ${k}`).join(", ")}</li>}
            {f.flagged_months?.filter((m) => m.confidence === "Low").map((m) => <li key={m.period} className="text-medium">{m.period}: {m.reasons[0]}</li>)}
          </ul>
        </Card>
      </div>
      {f.verified_reductions?.length ? (
        <Card className="mt-4">
          <CardTitle hint="IPMVP Option C-style, ASHRAE Guideline 14 checks, 90% interval. A C-Trace verification statement, not a carbon credit.">Verified reductions</CardTitle>
          <ul className="tabular text-sm">{f.verified_reductions.map((r) => <li key={r.activity}>{ACTIVITY_LABEL[r.activity]}: {n(r.avoided_tco2e, 1)} tCO₂e avoided (90% range {n(r.low, 1)}–{n(r.high, 1)})</li>)}</ul>
        </Card>
      ) : null}
      {d.include_documents && d.ledger && (
        <Card className="mt-4 overflow-x-auto">
          <CardTitle hint="The factory allowed you to open the bills behind each tonne.">Trace ledger</CardTitle>
          <table className="tabular w-full min-w-[560px] text-sm">
            <thead className="text-left text-xs text-slate"><tr><th className="py-1">Trace ID</th><th>Source</th><th className="text-right">Quantity</th><th className="text-right">tCO₂e</th><th>Bill</th></tr></thead>
            <tbody>{d.ledger.map((r) => (
              <tr key={r.trace_id} className="border-t border-line"><td className="py-1 font-mono text-xs">{r.trace_id}</td><td>{ACTIVITY_LABEL[r.activity]}</td><td className="text-right">{n(r.quantity)} {r.unit}</td><td className="text-right">{n(r.tco2e, 2)}</td>
                <td><a className="text-teal hover:underline" href={`/api/v1/shared/${encodeURIComponent(token)}/documents/${r.document_id}`} target="_blank" rel="noreferrer">{r.file_name}</a></td></tr>
            ))}</tbody>
          </table>
        </Card>
      )}
      <p className="mt-6 text-xs text-slate">Prepared with C-Trace from the factory&apos;s bills. Emission factors: {f.factors.join("; ")}.</p>
    </>
  );
}
