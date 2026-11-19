"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BadgeCheck, CircleX, Download, FileSignature, RefreshCw } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { ChartFrame } from "@/components/chart-frame";
import { Badge, Button, Card, CardTitle, Empty, ErrorState, Field, Loading, PageHeader, inputCls } from "@/components/ui";
import { api, download, post } from "@/lib/api";
import { ACTIVITY_LABEL, monthLabel, n, pct } from "@/lib/format";
import { qk, useFactory, useMe } from "@/lib/hooks";
import type { Reduction } from "@/lib/types";

const CHECK_LABEL: Record<string, string> = { cv_rmse: "CV(RMSE)", nmbe: "NMBE", r2: "R²", baseline_months: "Baseline months", interval_lower_bound: "90% lower bound" };

export default function ReductionsPage() {
  const { id } = useFactory();
  const me = useMe();
  const qc = useQueryClient();
  const list = useQuery({ queryKey: qk.reductions(id ?? ""), queryFn: () => api<Reduction[]>(`/v1/factories/${id}/reductions`), enabled: !!id });
  const compute = useMutation({ mutationFn: () => post<Reduction[]>(`/v1/factories/${id}/reductions/compute`), onSuccess: () => qc.invalidateQueries({ queryKey: ["reductions"] }) });
  const canRun = ["owner", "manager", "reviewer"].includes(me.data?.role ?? "");

  return (
    <div>
      <PageHeader
        title="Prove reductions"
        description="For each declared intervention, C-Trace fits a baseline of what the factory would have used (adjusted for production and weather), compares it with the bills afterwards, and only calls the saving verified when ASHRAE Guideline 14 checks pass and the 90% interval excludes zero."
        action={canRun && <Button onClick={() => compute.mutate()} busy={compute.isPending}><RefreshCw className="size-4" aria-hidden /> {list.data?.length ? "Recompute" : "Compute reductions"}</Button>}
      />
      {compute.error && <div className="mb-3"><ErrorState error={compute.error} /></div>}
      {compute.error && String((compute.error as Error).message).includes("verification") && (
        <p className="mb-3 text-sm"><Link className="text-teal hover:underline" href="/app/verify">Go to Verify →</Link></p>
      )}
      {list.isLoading ? <Loading /> : list.error ? <ErrorState error={list.error} retry={() => list.refetch()} /> : !list.data?.length ? (
        <Empty title="No reduction proofs yet" action={canRun && <Button onClick={() => compute.mutate()} busy={compute.isPending}>Compute reductions</Button>}>
          Accept corrections and run verification first: months graded Low are left out of the baseline.
        </Empty>
      ) : (
        <div className="space-y-4">{list.data.map((r) => <ReductionCard key={r.id} r={r} canSign={canRun} />)}</div>
      )}
    </div>
  );
}

function ReductionCard({ r, canSign }: { r: Reduction; canSign: boolean }) {
  const qc = useQueryClient();
  const [signing, setSigning] = useState(false);
  const [comment, setComment] = useState("");
  const [dl, setDl] = useState(false);
  const sign = useMutation({ mutationFn: () => post(`/v1/reductions/${r.id}/sign`, { comment }), onSuccess: () => { setSigning(false); qc.invalidateQueries({ queryKey: ["reductions"] }); } });
  const share = r.predicted ? r.avoided / r.predicted : null;
  return (
    <Card>
      <CardTitle
        hint={`${ACTIVITY_LABEL[r.activity]} · baseline ${monthLabel(r.baseline_from)}–${monthLabel(r.baseline_to)} (${r.n_baseline} mo) · reporting ${monthLabel(r.reporting_from)}–${monthLabel(r.reporting_to)} (${r.n_reporting} mo)`}
        action={
          <div className="flex items-center gap-2">
            {r.verified ? <Badge tone="teal"><BadgeCheck className="mr-1 size-3.5" aria-hidden />Verified</Badge> : <Badge tone="low"><CircleX className="mr-1 size-3.5" aria-hidden />Not verified</Badge>}
            {r.status === "signed" && <Badge tone="teal"><FileSignature className="mr-1 size-3.5" aria-hidden />Signed</Badge>}
          </div>
        }
      >
        {r.measure ?? "Intervention"}
      </CardTitle>
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
        <ChartFrame label={`Baseline model vs actual ${ACTIVITY_LABEL[r.activity]}`} height={260}
          table={{ head: ["Month", "Baseline", "Actual", "Used"], rows: r.curve.map((c) => [c.period, n(c.baseline), n(c.actual), c.used ? "yes" : "excluded"]) }}>
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={r.curve} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
              <CartesianGrid stroke="#e2e8f0" vertical={false} />
              <XAxis dataKey="period" tickFormatter={monthLabel} tick={{ fontSize: 11 }} minTickGap={16} />
              <YAxis tick={{ fontSize: 11 }} width={56} tickFormatter={(x: number) => `${Math.round(x / 1000)}k`} />
              <Tooltip formatter={(v, name) => [`${n(Number(v))} ${r.unit}`, String(name)]} labelFormatter={(l) => monthLabel(String(l))} />
              <Line dataKey="baseline" name="Baseline model" stroke="#475569" strokeDasharray="5 4" dot={false} isAnimationActive={false} />
              <Line dataKey="actual" name="Actual (bills)" stroke="#0F766E" strokeWidth={2} isAnimationActive={false} />
              <ReferenceLine x={r.reporting_from} stroke="#DC2626" strokeDasharray="4 3" label={{ value: "intervention", position: "insideTopLeft", fontSize: 10, fill: "#DC2626" }} />
            </LineChart>
          </ResponsiveContainer>
        </ChartFrame>
        <div>
          <dl className="tabular grid grid-cols-2 gap-3 text-sm">
            <div className="col-span-2 rounded-lg bg-teal-light p-3">
              <dt className="text-slate">Avoided</dt>
              <dd className="text-2xl font-semibold text-teal">{n(r.avoided)} {r.unit} <span className="text-base font-normal text-slate">({pct(share)})</span></dd>
              <dd className="text-xs text-slate">90% interval {n(r.low)} – {n(r.high)} {r.unit}</dd>
            </div>
            <div><dt className="text-slate">Emissions avoided</dt><dd className="font-semibold">{n(r.tco2e, 1)} tCO₂e</dd><dd className="text-xs text-slate">{n(r.tco2e_low, 1)}–{n(r.tco2e_high, 1)}</dd></div>
            <div><dt className="text-slate">Energy cost avoided</dt><dd className="font-semibold">{r.bdt_saved === null ? "—" : `BDT ${n(r.bdt_saved)}`}</dd><dd className="text-xs text-slate">at BDT {n(r.tariff_bdt, 2)}/{r.unit}</dd></div>
          </dl>
          <table className="tabular mt-3 w-full text-sm">
            <caption className="sr-only">Statistical checks</caption>
            <tbody>
              {Object.entries(r.checks).map(([k, c]) => (
                <tr key={k} className="border-t border-line">
                  <th scope="row" className="py-1.5 text-left font-normal text-slate">{CHECK_LABEL[k] ?? k}</th>
                  <td className="py-1.5">{k === "cv_rmse" || k === "nmbe" ? pct(c.value) : n(c.value, k === "r2" ? 3 : 0)}</td>
                  <td className="py-1.5 text-xs text-slate">{c.limit}</td>
                  <td className="py-1.5 text-right">{c.pass ? <Badge tone="teal">pass</Badge> : <Badge tone="low">fail</Badge>}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {(r.excluded.baseline.length > 0 || r.excluded.reporting.length > 0) && (
            <p className="mt-2 text-xs text-medium">Left out (graded Low): {[...r.excluded.baseline, ...r.excluded.reporting].join(", ")}</p>
          )}
          <p className="mt-2 text-xs text-slate">{r.status_reason}</p>
        </div>
      </div>
      <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-line pt-4">
        <Button variant="secondary" busy={dl} onClick={async () => { setDl(true); try { await download(`/v1/reductions/${r.id}/certificate.pdf`, "C-Trace_reduction.pdf"); } finally { setDl(false); } }}>
          <Download className="size-4" aria-hidden /> {r.status === "signed" ? "Reduction certificate (PDF)" : "Draft certificate (PDF)"}
        </Button>
        {r.status === "signed" && <span className="text-sm text-slate">Signed {r.signed_at ? new Date(r.signed_at).toLocaleDateString() : ""}: “{r.sign_comment}”</span>}
        {r.status === "draft" && r.verified && canSign && !signing && <Button onClick={() => setSigning(true)}><FileSignature className="size-4" aria-hidden /> Sign off</Button>}
      </div>
      {signing && (
        <form className="mt-3 space-y-2" onSubmit={(e) => { e.preventDefault(); sign.mutate(); }}>
          <Field label="What did you check before signing? (printed on the certificate and kept in the audit log)">
            <input className={inputCls} value={comment} onChange={(e) => setComment(e.target.value)} minLength={3} required placeholder="e.g. Checked baseline bills and the economiser commissioning report" />
          </Field>
          {sign.error && <ErrorState error={sign.error} />}
          <div className="flex gap-2"><Button type="submit" busy={sign.isPending}>Sign reduction</Button><Button type="button" variant="ghost" onClick={() => setSigning(false)}>Cancel</Button></div>
        </form>
      )}
    </Card>
  );
}
