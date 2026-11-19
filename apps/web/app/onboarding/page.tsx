"use client";

import { useQueryClient } from "@tanstack/react-query";
import Image from "next/image";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { PickMap } from "@/components/pick-map";
import { Button, Card, ErrorState, Field, inputCls } from "@/components/ui";
import { ApiError, post } from "@/lib/api";
import { useMe, useSession } from "@/lib/hooks";
import type { Factory } from "@/lib/types";

const CLUSTERS = [
  { name: "Gazipur / Savar", lat: 24.0, lon: 90.42 },
  { name: "Dhaka / Narayanganj", lat: 23.62, lon: 90.5 },
  { name: "Chattogram", lat: 22.35, lon: 91.83 },
  { name: "Mymensingh / Bhaluka", lat: 24.38, lon: 90.38 },
];

export default function Onboarding() {
  const session = useSession();
  const me = useMe();
  const router = useRouter();
  const qc = useQueryClient();
  const needsOrg = me.error instanceof ApiError && me.error.status === 403;
  const [org, setOrg] = useState("");
  const [f, setF] = useState({ name: "", location: "", factory_type: "knit_composite", workers: "", has_wet_processing: true, captive_gas_gen: false });
  const [lat, setLat] = useState<number | null>(null);
  const [lon, setLon] = useState<number | null>(null);
  const [solar, setSolar] = useState({ kwp: "", from: "" });
  const [iv, setIv] = useState({ measure: "", from: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  if (session === null) { router.replace("/login"); return null; }

  async function submit() {
    setBusy(true); setError(null);
    try {
      if (needsOrg) await post("/v1/onboarding", { organization_name: org });
      await qc.invalidateQueries();
      const fac = await post<Factory>("/v1/factories", { name: f.name, location: f.location || null, lat, lon, factory_type: f.factory_type, workers: Number(f.workers), has_wet_processing: f.has_wet_processing, captive_gas_gen: f.captive_gas_gen });
      if (solar.kwp && solar.from) await post(`/v1/factories/${fac.id}/assets`, { kind: "solar", capacity: Number(solar.kwp), unit: "kWp", from_period: solar.from });
      if (iv.measure && iv.from) await post(`/v1/factories/${fac.id}/interventions`, { measure: iv.measure, from_period: iv.from });
      await qc.invalidateQueries();
      router.replace("/app/upload");
    } catch (e) {
      setError(e);
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto max-w-3xl px-4 py-8 sm:px-6">
      <Image src="/brand/c-trace-logo.svg" alt="C-Trace" width={140} height={30} />
      <h1 className="mt-6 text-2xl font-semibold">Set up your factory</h1>
      <p className="mt-1 text-sm text-slate">The profile tells the C-Trace Verifier what a normal month looks like: type, size, processes, location (for weather) and the changes you have made.</p>
      <form className="mt-6 space-y-4" onSubmit={(e) => { e.preventDefault(); submit(); }}>
        {needsOrg && (
          <Card><Field label="Organisation name"><input className={inputCls} required minLength={2} value={org} onChange={(e) => setOrg(e.target.value)} placeholder="e.g. Your company Ltd." /></Field></Card>
        )}
        <Card className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Factory name"><input className={inputCls} required value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
            <Field label="Type">
              <select className={inputCls} value={f.factory_type} onChange={(e) => setF({ ...f, factory_type: e.target.value })}>
                <option value="knit_composite">Knit composite</option><option value="woven">Woven</option><option value="denim_wash">Denim / washing</option><option value="sweater">Sweater</option>
              </select>
            </Field>
            <Field label="Workers"><input className={inputCls} required type="number" min={1} value={f.workers} onChange={(e) => setF({ ...f, workers: e.target.value })} /></Field>
            <Field label="Location (town, district)"><input className={inputCls} value={f.location} onChange={(e) => setF({ ...f, location: e.target.value })} /></Field>
          </div>
          <div className="flex flex-wrap gap-4 text-sm">
            <label className="flex items-center gap-2"><input type="checkbox" checked={f.has_wet_processing} onChange={(e) => setF({ ...f, has_wet_processing: e.target.checked })} /> Wet processing (dyeing, washing)</label>
            <label className="flex items-center gap-2"><input type="checkbox" checked={f.captive_gas_gen} onChange={(e) => setF({ ...f, captive_gas_gen: e.target.checked })} /> Captive gas generator</label>
          </div>
        </Card>
        <Card>
          <p className="mb-2 text-sm font-medium">Where is the factory? <span className="font-normal text-slate">Click the map or pick a cluster — used for NASA POWER weather.</span></p>
          <div className="mb-3 flex flex-wrap gap-2">{CLUSTERS.map((c) => <Button key={c.name} type="button" size="sm" variant="secondary" onClick={() => { setLat(c.lat); setLon(c.lon); }}>{c.name}</Button>)}</div>
          <PickMap lat={lat} lon={lon} onPick={(a, b) => { setLat(a); setLon(b); }} />
          <p className="tabular mt-2 text-sm text-slate">{lat !== null && lon !== null ? `${lat.toFixed(3)}° N, ${lon.toFixed(3)}° E` : "No location yet"}</p>
        </Card>
        <Card className="grid gap-3 sm:grid-cols-2">
          <Field label="Rooftop solar (kWp)" hint="Leave empty if none"><input className={inputCls} type="number" min={0} value={solar.kwp} onChange={(e) => setSolar({ ...solar, kwp: e.target.value })} /></Field>
          <Field label="Solar online from (YYYY-MM)"><input className={inputCls} pattern="\d{4}-(0[1-9]|1[0-2])" value={solar.from} onChange={(e) => setSolar({ ...solar, from: e.target.value })} /></Field>
          <Field label="An efficiency measure you made" hint="e.g. Boiler economiser + steam-trap repair"><input className={inputCls} value={iv.measure} onChange={(e) => setIv({ ...iv, measure: e.target.value })} /></Field>
          <Field label="Started (YYYY-MM)"><input className={inputCls} pattern="\d{4}-(0[1-9]|1[0-2])" value={iv.from} onChange={(e) => setIv({ ...iv, from: e.target.value })} /></Field>
        </Card>
        {error ? <ErrorState error={error} /> : null}
        <Button type="submit" busy={busy}>Create factory and upload bills</Button>
      </form>
    </div>
  );
}
