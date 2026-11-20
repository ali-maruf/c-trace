"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { Bar, CartesianGrid, ComposedChart, Legend, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { ChartFrame } from "@/components/chart-frame";
import { Badge, ButtonLink, Card, CardTitle, Empty, ErrorState, PageHeader, Skeleton, Stat, inputCls } from "@/components/ui";
import { api } from "@/lib/api";
import { ACTIVITY_COLOR, ACTIVITY_LABEL, monthLabel, n, t } from "@/lib/format";
import { qk, useFactory } from "@/lib/hooks";
import type { Benchmark, Summary } from "@/lib/types";

export default function Dashboard() {
  const { id, factory, isLoading, error, noFactory } = useFactory();
  const [from, setFrom] = useState<string>("");
  const [to, setTo] = useState<string>("");
  const router = useRouter();
  const all = useQuery({ queryKey: qk.summary(id ?? "", "", ""), queryFn: () => api<Summary>(`/v1/factories/${id}/summary`), enabled: !!id });
  const params = new URLSearchParams({ ...(from && { period_from: from }), ...(to && { period_to: to }) }).toString();
  const sel = useQuery({ queryKey: qk.summary(id ?? "", from, to), queryFn: () => api<Summary>(`/v1/factories/${id}/summary${params ? `?${params}` : ""}`), enabled: !!id });
  const bench = useQuery({ queryKey: ["benchmark", id], queryFn: () => api<Benchmark>(`/v1/factories/${id}/benchmark`), enabled: !!id, retry: false });
  const periods = all.data?.months.map((m) => m.period) ?? [];
  const months = useMemo(() => sel.data?.months ?? [], [sel.data]);

  if (noFactory) return <Empty title="No factory yet" action={<ButtonLink href="/onboarding">Set up your factory</ButtonLink>}>Create a factory profile to start uploading bills.</Empty>;
  if (error) return <ErrorState error={error} />;
  const tot = sel.data?.totals;
  const ivs = factory?.interventions ?? [];

  return (
    <div>
      <PageHeader
        title={factory?.name ?? "Dashboard"}
        description={factory ? `${factory.location ?? ""} · ${factory.factory_type.replace("_", " ")} · ${n(factory.workers)} workers` : undefined}
        action={
          <div className="flex items-end gap-2 text-sm">
            <label className="text-slate">From
              <select className={`${inputCls} mt-1 w-32`} value={from} onChange={(e) => setFrom(e.target.value)} aria-label="Period from">
                <option value="">Start</option>{periods.map((p) => <option key={p} value={p}>{monthLabel(p)}</option>)}
              </select>
            </label>
            <label className="text-slate">To
              <select className={`${inputCls} mt-1 w-32`} value={to} onChange={(e) => setTo(e.target.value)} aria-label="Period to">
                <option value="">Latest</option>{periods.map((p) => <option key={p} value={p}>{monthLabel(p)}</option>)}
              </select>
            </label>
          </div>
        }
      />
      {sel.error && <ErrorState error={sel.error} retry={() => sel.refetch()} />}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {isLoading || sel.isLoading || !tot ? (
          [0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-[104px]" />)
        ) : (
          <>
            <Stat label="Total Scope 1 + 2" value={t(tot.total_tco2e)} tone="teal" sub={`${tot.months} months · ${sel.data?.period_from ? monthLabel(sel.data.period_from) : ""} – ${sel.data?.period_to ? monthLabel(sel.data.period_to) : ""}`} />
            <Stat label="Scope 1 (gas, diesel)" value={t(tot.scope1_tco2e)} sub="IPCC 2006 defaults, AR6 GWP" />
            <Stat label="Scope 2, location-based" value={t(tot.scope2_location_tco2e)}
              sub={tot.scope2_statutory ? `Statutory: ${t(tot.scope2_statutory.scope2_statutory_tco2e)} (${tot.scope2_statutory.months_covered} mo)` : "Ember grid intensity, Bangladesh"} />
            <Stat label="Intensity" value={tot.kgco2e_per_piece ? `${n(tot.kgco2e_per_piece, 3)} kg/piece` : "—"} sub={tot.pieces ? `${n(tot.pieces)} pieces` : "Add production to see intensity"} />
          </>
        )}
      </div>
      {sel.data && (
        <p className="mt-2 text-sm text-slate">
          Calculated from {n(sel.data.data_status.documents)} bills
          {sel.data.data_status.unconfirmed_records ? ` (${n(sel.data.data_status.unconfirmed_records)} not yet confirmed by a person)` : ""}.{" "}
          <Link href="/app/bills" className="font-medium text-teal hover:underline">See every bill →</Link>
        </p>
      )}

      <div className="mt-4 grid gap-4 lg:grid-cols-[1fr_320px]">
        <Card>
          <CardTitle hint="Click a month to trace its tonnes to the bills. Dashed lines mark declared interventions.">Monthly emissions by source</CardTitle>
          {sel.isLoading ? <Skeleton className="h-[300px]" /> : months.length === 0 ? <Empty title="No emissions yet">Upload bills to build the ledger.</Empty> : (
            <ChartFrame
              label={`Monthly emissions by source and intensity per piece, ${months.length} months`}
              table={{ head: ["Month", "Gas t", "Grid t", "Diesel t", "Total t", "kg/piece"], rows: months.map((m) => [m.period, n(m.natural_gas, 1), n(m.grid_electricity, 1), n(m.diesel, 1), n(m.total_t, 1), n(m.kgco2e_per_piece, 3)]) }}
            >
              <ResponsiveContainer width="100%" height="100%">
                <ComposedChart data={months} margin={{ top: 10, right: 8, bottom: 0, left: 0 }}
                  onClick={(e) => { const p = (e as { activeLabel?: string } | null)?.activeLabel; if (p) router.push(`/app/trace?period=${p}`); }}>
                  <CartesianGrid stroke="#e2e8f0" vertical={false} />
                  <XAxis dataKey="period" tickFormatter={monthLabel} tick={{ fontSize: 11 }} minTickGap={16} />
                  <YAxis yAxisId="t" tick={{ fontSize: 11 }} width={48} label={{ value: "tCO₂e", angle: -90, position: "insideLeft", fontSize: 11 }} />
                  <YAxis yAxisId="i" orientation="right" tick={{ fontSize: 11 }} width={44} />
                  <Tooltip formatter={(v, name) => [String(name) === "kg CO₂e / piece" ? n(Number(v), 3) : `${n(Number(v), 1)} t`, String(name)]} labelFormatter={(l) => monthLabel(String(l))} />
                  <Legend wrapperStyle={{ fontSize: 12 }} />
                  {(["natural_gas", "grid_electricity", "diesel"] as const).map((a) => (
                    <Bar key={a} yAxisId="t" dataKey={a} name={ACTIVITY_LABEL[a]} stackId="s" fill={ACTIVITY_COLOR[a]} cursor="pointer" isAnimationActive={false} />
                  ))}
                  <Line yAxisId="i" dataKey="kgco2e_per_piece" name="kg CO₂e / piece" stroke="#111827" strokeWidth={1.5} dot={false} isAnimationActive={false} />
                  {ivs.map((iv, i) => (
                    <ReferenceLine key={iv.id} yAxisId="t" x={iv.from_period} stroke="#DC2626" strokeDasharray="4 3"
                      label={{ value: iv.measure.split(/[ +]/)[0], position: i % 2 ? "insideBottomLeft" : "insideTopLeft", fontSize: 10, fill: "#DC2626" }} />
                  ))}
                </ComposedChart>
              </ResponsiveContainer>
            </ChartFrame>
          )}
        </Card>

        <div className="space-y-4">
          <Card>
            <CardTitle>Data status</CardTitle>
            {sel.data && (
              <dl className="space-y-2 text-sm">
                <div className="flex justify-between"><dt className="text-slate">Source documents</dt><dd className="tabular font-medium">{sel.data.data_status.documents}</dd></div>
                <div className="flex justify-between"><dt className="text-slate">Awaiting review</dt><dd className="tabular font-medium">{sel.data.data_status.unconfirmed_records}</dd></div>
                {Object.entries(sel.data.data_status.records_by_status).filter(([k]) => k !== "extracted").map(([k, v]) => (
                  <div key={k} className="flex justify-between"><dt className="capitalize text-slate">{k}</dt><dd className="tabular font-medium">{v}</dd></div>
                ))}
              </dl>
            )}
            <div className="mt-4 flex flex-wrap gap-2">
              <ButtonLink href="/app/review" variant="secondary">Review bills</ButtonLink>
              <ButtonLink href="/app/verify" variant="secondary">Verify months</ButtonLink>
            </div>
          </Card>
          {bench.data && (
            <Card>
              <CardTitle hint={`Climate TRACE ${bench.data.year}, ${n(bench.data.n_facilities)} Bangladeshi textile & garment facilities`}>Sector context</CardTitle>
              <p className="text-sm">
                Annualised Scope 1 of <strong className="tabular">{t(bench.data.factory_scope1_tco2e_per_year)}</strong>{" "}
                {(bench.data.factory_percentile ?? 0) >= 99.5
                  ? <>is higher than every modelled facility estimate (median {t(bench.data.quantiles_tco2e.p50)}, 90th percentile {t(bench.data.quantiles_tco2e.p90)}).</>
                  : <>is above <strong className="tabular">{n(bench.data.factory_percentile, 0)}%</strong> of modelled facility estimates (median {t(bench.data.quantiles_tco2e.p50)}).</>}
              </p>
              <p className="mt-2 text-xs text-slate">{bench.data.caveat}</p>
              <p className="mt-2 text-xs text-slate">
                <Badge>{bench.data.licence?.split(" (")[0] ?? "CC BY 4.0"}</Badge>{" "}
                {bench.data.source_url && <a className="text-teal hover:underline" href="https://climatetrace.org" target="_blank" rel="noreferrer">Climate TRACE</a>}
              </p>
            </Card>
          )}
          <Card>
            <CardTitle>Shortcuts</CardTitle>
            <ul className="space-y-1.5 text-sm">
              <li><Link className="text-teal hover:underline" href="/app/reductions">Prove the boiler retrofit and solar savings →</Link></li>
              <li><Link className="text-teal hover:underline" href="/app/reports">Export a buyer or lender report →</Link></li>
              <li><Link className="text-teal hover:underline" href="/app/copilot">Ask the Copilot why a month changed →</Link></li>
            </ul>
          </Card>
        </div>
      </div>
    </div>
  );
}
