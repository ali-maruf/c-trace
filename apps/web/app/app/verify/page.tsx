"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { useMemo, useState } from "react";
import { Area, Bar, BarChart, Cell, ComposedChart, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis, CartesianGrid } from "recharts";
import { ChartFrame } from "@/components/chart-frame";
import { Button, Card, CardTitle, ConfidencePill, Empty, ErrorState, Field, Loading, PageHeader, Stat, inputCls } from "@/components/ui";
import { ApiError, api, post } from "@/lib/api";
import { TARGET_LABEL, UNIT_OF, monthLabel, n } from "@/lib/format";
import { qk, useFactory, useMe } from "@/lib/hooks";
import type { Confidence, Target, Verification, VerificationResult } from "@/lib/types";

const TARGETS: Target[] = ["gas_m3", "grid_kwh", "diesel_l"];
const CONF_COLOR: Record<Confidence, string> = { High: "#0F766E", Medium: "#D97706", Low: "#DC2626" };

export default function VerifyPage() {
  const { id } = useFactory();
  const me = useMe();
  const qc = useQueryClient();
  const [target, setTarget] = useState<Target>("gas_m3");
  const [selected, setSelected] = useState<string | null>(null);
  const v = useQuery({ queryKey: qk.verification(id ?? ""), queryFn: () => api<Verification>(`/v1/factories/${id}/verification`), enabled: !!id, retry: false });
  const run = useMutation({ mutationFn: () => post<Verification>(`/v1/factories/${id}/verification/run`), onSuccess: (d) => { qc.setQueryData(qk.verification(id ?? ""), d); qc.invalidateQueries({ queryKey: ["reductions"] }); } });
  const canRun = ["owner", "manager", "reviewer"].includes(me.data?.role ?? "");
  const notRun = v.error instanceof ApiError && v.error.status === 404;
  const results = useMemo(() => v.data?.results ?? [], [v.data]);
  const sel = results.find((r) => r.period === selected) ?? results.find((r) => r.confidence === "Low") ?? null;
  const data = useMemo(() => results.map((r) => {
    const c = r.z[target];
    return { period: r.period, expected: c.expected, reported: c.reported, lo: c.expected_lo, band: c.expected_hi !== null && c.expected_lo !== null ? c.expected_hi - c.expected_lo : null, confidence: r.confidence };
  }), [results, target]);

  const header = (
    <PageHeader
      title="Verify"
      description="The C-Trace Verifier predicts what each month should have used, given production, weather and assets, and grades the bills against it. A flag means “please check this document”, never an accusation."
      action={canRun && <Button onClick={() => run.mutate()} busy={run.isPending}><RefreshCw className="size-4" aria-hidden /> {v.data ? "Run again" : "Run verification"}</Button>}
    />
  );
  if (v.isLoading) return <>{header}<Loading /></>;
  if (notRun) return <>{header}{run.error && <ErrorState error={run.error} />}<Empty title="Not verified yet" action={canRun && <Button onClick={() => run.mutate()} busy={run.isPending}>Run verification</Button>}>Run the C-Trace Verifier on the bills and production you have so far.</Empty></>;
  if (v.error) return <>{header}<ErrorState error={v.error} retry={() => v.refetch()} /></>;
  if (!v.data) return null;

  return (
    <div>
      {header}
      {run.error && <div className="mb-3"><ErrorState error={run.error} /></div>}
      {v.data.stale && <p role="status" className="mb-3 rounded-md border border-medium/30 bg-medium-bg px-3 py-2 text-sm text-medium">Bills changed since this run (for example a correction was accepted). Run verification again to update the grades.</p>}
      <div className="grid gap-3 sm:grid-cols-3">
        <Stat label="High confidence months" value={v.data.counts.High} tone="teal" />
        <Stat label="Medium — worth a look" value={v.data.counts.Medium} />
        <Stat label="Low — human review" value={v.data.counts.Low} sub={`${v.data.run.model_version} · ${new Date(v.data.run.created_at).toLocaleString()}`} />
      </div>
      <Card className="mt-4">
        <CardTitle hint="Select a month to see why it was graded that way.">Confidence by month</CardTitle>
        <div className="flex flex-wrap gap-1.5">
          {results.map((r) => <ConfidencePill key={r.period} value={r.confidence} selected={sel?.period === r.period} onClick={() => setSelected(r.period)}>{r.period}</ConfidencePill>)}
        </div>
      </Card>

      <div className="mt-4 grid gap-4 xl:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
        <Card>
          <CardTitle hint="Shaded: 90% expected range (calibrated on factories the model never saw). Dots: what the bills say, coloured by grade.">Expected vs reported</CardTitle>
          <div className="mb-3 flex flex-wrap gap-1" role="tablist" aria-label="Energy source">
            {TARGETS.map((t) => <button key={t} role="tab" type="button" aria-selected={target === t} onClick={() => setTarget(t)} className={`rounded-md px-3 py-1 text-sm ${target === t ? "bg-teal text-white" : "border border-line"}`}>{TARGET_LABEL[t]} ({UNIT_OF[t]})</button>)}
          </div>
          <ChartFrame label={`${TARGET_LABEL[target]}: expected range and reported use by month`}
            table={{ head: ["Month", "Expected", "Range", "Reported", "Grade"], rows: data.map((d) => [d.period, n(d.expected), d.lo === null || d.band === null ? "—" : `${n(d.lo)}–${n(d.lo + d.band)}`, n(d.reported), d.confidence]) }}>
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }} onClick={(e) => { const p = (e as { activeLabel?: string } | null)?.activeLabel; if (p) setSelected(p); }}>
                <CartesianGrid stroke="#e2e8f0" vertical={false} />
                <XAxis dataKey="period" tickFormatter={monthLabel} tick={{ fontSize: 11 }} minTickGap={16} />
                <YAxis tick={{ fontSize: 11 }} width={56} tickFormatter={(x: number) => (x >= 1000 ? `${Math.round(x / 1000)}k` : String(x))} />
                <Tooltip content={({ active, payload, label }) => {
                  const d = active && payload?.[0] ? (payload[0].payload as { expected: number | null; reported: number | null; lo: number | null; band: number | null; confidence: Confidence }) : null;
                  if (!d) return null;
                  return (
                    <div className="tabular rounded-md border border-line bg-white px-3 py-2 text-xs shadow">
                      <p className="font-medium">{monthLabel(String(label))} · {d.confidence}</p>
                      <p>Reported: {n(d.reported)} {UNIT_OF[target]}</p>
                      <p className="text-slate">Expected: {n(d.expected)}{d.lo !== null && d.band !== null && <> (90% range {n(d.lo)}–{n(d.lo + d.band)})</>}</p>
                    </div>
                  );
                }} />
                <Area dataKey="lo" stackId="b" stroke="none" fill="transparent" isAnimationActive={false} legendType="none" name="range start" />
                <Area dataKey="band" stackId="b" stroke="none" fill="#0F766E" fillOpacity={0.12} isAnimationActive={false} name="90% expected range" />
                <Line dataKey="expected" stroke="#475569" strokeDasharray="5 4" dot={false} isAnimationActive={false} name="Expected" />
                <Line dataKey="reported" stroke="#0F766E" strokeWidth={1.8} isAnimationActive={false} name="Reported (bills)"
                  dot={(p: { cx?: number; cy?: number; payload?: { confidence: Confidence; period: string } }) => p.cx === undefined || p.cy === undefined || !p.payload ? <g key={Math.random()} /> :
                    <circle key={p.payload.period} cx={p.cx} cy={p.cy} r={p.payload.period === sel?.period ? 6 : 4} fill={CONF_COLOR[p.payload.confidence]} stroke="white" strokeWidth={1} />} />
                {sel && <ReferenceLine x={sel.period} stroke="#111827" strokeOpacity={0.25} />}
              </ComposedChart>
            </ResponsiveContainer>
          </ChartFrame>
        </Card>
        {sel ? <MonthDetail r={sel} target={target} runId={v.data.run.id} canResolve={canRun} /> : <Card><p className="text-sm text-slate">Select a month.</p></Card>}
      </div>

      <Card className="mt-4">
        <CardTitle>Why these months were flagged</CardTitle>
        <ul className="space-y-2 text-sm">
          {results.filter((r) => r.confidence !== "High").map((r) => (
            <li key={r.period} className="flex flex-wrap items-start gap-2">
              <ConfidencePill value={r.confidence} onClick={() => setSelected(r.period)}>{r.period}</ConfidencePill>
              <span className="min-w-0 flex-1">{r.reasons.join(" · ")}</span>
              {r.resolution && <span className="text-xs text-teal">Resolved: {r.resolution}</span>}
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}

function MonthDetail({ r, target, runId, canResolve }: { r: VerificationResult; target: Target; runId: string; canResolve: boolean }) {
  const c = r.z[target];
  const d = c.drivers;
  // Waterfall on a zoomed axis: contributions are small next to the totals, so the axis spans the path, not 0…total.
  const { steps, lo, hi } = d ? (() => {
    const path = [d.typical];
    for (const k of ["production", "weather", "solar", "season"] as const) path.push(path.at(-1)! + d[k]);
    const min = Math.min(...path), max = Math.max(...path);
    const pad = Math.max((max - min) * 0.35, Math.abs(max) * 0.01, 1);
    const lo0 = Math.max(0, min - pad), hi0 = max + pad;
    let acc = d.typical;
    const out: { name: string; base: number; value: number; total: boolean; sign?: number; shown: number }[] = [{ name: "Typical", base: lo0, value: d.typical - lo0, total: true, shown: d.typical }];
    for (const k of ["production", "weather", "solar", "season"] as const) {
      const v = d[k];
      out.push({ name: k[0]!.toUpperCase() + k.slice(1), base: v >= 0 ? acc : acc + v, value: Math.abs(v), total: false, sign: v, shown: v });
      acc += v;
    }
    out.push({ name: "Expected", base: lo0, value: acc - lo0, total: true, shown: acc });
    return { steps: out, lo: lo0, hi: hi0 };
  })() : { steps: [], lo: 0, hi: 0 };
  return (
    <Card>
      <CardTitle hint={`${TARGET_LABEL[target]} · z = ${c.z === null ? "—" : c.z.toFixed(1)}`} action={<ConfidencePill value={r.confidence} />}>{monthLabel(r.period)}</CardTitle>
      <ul className="mb-4 space-y-1 text-sm">{r.reasons.map((x) => <li key={x}>• {x}</li>)}</ul>
      <dl className="tabular mb-3 grid grid-cols-3 gap-2 text-sm">
        <div><dt className="text-slate">Reported</dt><dd className="font-semibold">{n(c.reported)} {UNIT_OF[target]}</dd></div>
        <div><dt className="text-slate">Expected</dt><dd className="font-semibold">{n(c.expected)}</dd></div>
        <div><dt className="text-slate">90% range</dt><dd>{n(c.expected_lo)}–{n(c.expected_hi)}</dd></div>
      </dl>
      {d && (
        <>
          <p className="mb-1 text-sm font-medium">Why this month&apos;s expected use differs from a typical month</p>
          <ChartFrame label={`Driver waterfall for ${r.period}`} height={200}
            table={{ head: ["Driver", UNIT_OF[target] ?? ""], rows: [["Typical month", n(d.typical)], ["Production", n(d.production)], ["Weather", n(d.weather)], ["Solar", n(d.solar)], ["Season", n(d.season)], ["Expected", n(c.expected)]] }}>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={steps} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
                <XAxis dataKey="name" tick={{ fontSize: 10 }} interval={0} />
                <YAxis domain={[lo, hi]} allowDataOverflow tick={{ fontSize: 10 }} width={52} tickFormatter={(x: number) => (x >= 1000 ? `${Math.round(x / 1000)}k` : String(Math.round(x)))} />
                <Tooltip cursor={{ fill: "#f1f5f9" }} content={({ active, payload }) => {
                  const st = active && payload?.[0] ? (payload[0].payload as { name: string; total: boolean; shown: number }) : null;
                  if (!st) return null;
                  return <div className="tabular rounded-md border border-line bg-white px-2 py-1 text-xs shadow">{st.name}: {st.total ? n(st.shown) : `${st.shown >= 0 ? "+" : "−"}${n(Math.abs(st.shown))}`} {UNIT_OF[target]}</div>;
                }} />
                <Bar dataKey="base" stackId="w" fill="transparent" isAnimationActive={false} />
                <Bar dataKey="value" stackId="w" isAnimationActive={false}>
                  {steps.map((s) => <Cell key={s.name} fill={s.total ? "#475569" : (s.sign ?? 0) >= 0 ? "#D97706" : "#0F766E"} />)}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </ChartFrame>
          <p className="mt-1 text-xs text-slate">Exact Shapley contributions; they add up to the expected value. Amber raises expected use, teal lowers it. The axis does not start at zero.</p>
        </>
      )}
      {r.confidence !== "High" && <Resolve r={r} runId={runId} canResolve={canResolve} />}
    </Card>
  );
}

function Resolve({ r, runId, canResolve }: { r: VerificationResult; runId: string; canResolve: boolean }) {
  const qc = useQueryClient();
  const [resolution, setResolution] = useState("evidence_requested");
  const [comment, setComment] = useState("");
  const m = useMutation({ mutationFn: () => post(`/v1/verification/${runId}/${r.period}/resolve`, { resolution, comment }), onSuccess: () => { setComment(""); qc.invalidateQueries({ queryKey: ["verification"] }); } });
  if (r.resolution) return <p className="mt-4 rounded-md bg-teal-light px-3 py-2 text-sm text-teal-dark">Resolved {r.resolved_at ? new Date(r.resolved_at).toLocaleDateString() : ""}: {r.resolution}</p>;
  if (!canResolve) return null;
  return (
    <form className="mt-4 space-y-2 border-t border-line pt-4" onSubmit={(e) => { e.preventDefault(); m.mutate(); }}>
      <Field label="Reviewer resolution">
        <select className={inputCls} value={resolution} onChange={(e) => setResolution(e.target.value)}>
          <option value="evidence_requested">Evidence requested from the factory</option>
          <option value="confirmed_correct">Checked — the bill is correct</option>
          <option value="corrected">Corrected in the review queue</option>
          <option value="not_an_issue">Not an issue (explained)</option>
        </select>
      </Field>
      <Field label="Comment (kept in the audit log)"><input className={inputCls} value={comment} onChange={(e) => setComment(e.target.value)} minLength={3} required placeholder="What did you check?" /></Field>
      {m.error && <ErrorState error={m.error} />}
      <Button type="submit" size="sm" busy={m.isPending}>Save resolution</Button>
    </form>
  );
}
