"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, ExternalLink, Trash2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Badge, Button, Card, CardTitle, ErrorState, Field, Loading, PageHeader, inputCls } from "@/components/ui";
import { ApiError, api, download, post } from "@/lib/api";
import { ACTIVITY_LABEL, n } from "@/lib/format";
import { useFactory, useMe } from "@/lib/hooks";
import { clearSession } from "@/lib/session";
import type { Factory, Role } from "@/lib/types";

const ROLES: Role[] = ["owner", "manager", "reviewer", "viewer", "buyer", "lender"];
type Members = { members: { user_id: string; role: Role; email: string; display_name: string | null; created_at: string }[]; invites: { id: string; email: string; role: Role; created_at: string }[] };
type Factor = { id: string; activity: string; region: string; year: number | null; kgco2e_per_unit: number; unit: string; source: string; source_url: string; version: string; kind: string; valid_from: string | null; valid_to: string | null };

export default function SettingsPage() {
  const { id, factory } = useFactory();
  const me = useMe();
  const isOwner = me.data?.role === "owner";
  return (
    <div className="space-y-4">
      <PageHeader title="Settings" />
      {factory && <ProfileCard factory={factory} canEdit={["owner", "manager"].includes(me.data?.role ?? "")} />}
      <MembersCard isOwner={isOwner} isDemo={!!me.data?.memberships.find((m) => m.org_id === me.data?.org_id)?.is_demo} />
      <FactorsCard />
      {id && factory && <DataCard factoryId={id} name={factory.name} isOwner={isOwner} canExport={["owner", "manager"].includes(me.data?.role ?? "")} />}
    </div>
  );
}

function ProfileCard({ factory, canEdit }: { factory: Factory; canEdit: boolean }) {
  const qc = useQueryClient();
  const [iv, setIv] = useState({ measure: "", from_period: "", affects: "" });
  const add = useMutation({ mutationFn: () => post(`/v1/factories/${factory.id}/interventions`, { measure: iv.measure, from_period: iv.from_period, ...(iv.affects && { affects: [iv.affects] }) }),
    onSuccess: () => { setIv({ measure: "", from_period: "", affects: "" }); qc.invalidateQueries({ queryKey: ["factory"] }); } });
  return (
    <Card>
      <CardTitle hint="What the C-Trace Verifier and the reduction proofs know about this factory.">Factory profile</CardTitle>
      <dl className="tabular grid gap-3 text-sm sm:grid-cols-4">
        <div><dt className="text-slate">Name</dt><dd className="font-medium">{factory.name}</dd></div>
        <div><dt className="text-slate">Type · workers</dt><dd>{factory.factory_type.replace("_", " ")} · {n(factory.workers)}</dd></div>
        <div><dt className="text-slate">Processes</dt><dd>{[factory.has_wet_processing && "wet processing", factory.captive_gas_gen && "captive gas generation"].filter(Boolean).join(", ") || "—"}</dd></div>
        <div><dt className="text-slate">Location</dt><dd>{factory.location ?? "—"} {factory.lat !== null && factory.lon !== null && <span className="text-slate">({factory.lat.toFixed(2)}, {factory.lon.toFixed(2)})</span>}</dd></div>
      </dl>
      <h3 className="mt-4 text-sm font-medium">Assets and interventions</h3>
      <ul className="mt-1 space-y-1 text-sm">
        {factory.assets?.map((a) => <li key={a.id}>{a.kind}: {n(a.capacity)} {a.unit} from {a.from_period}</li>)}
        {factory.interventions?.map((i) => <li key={i.id}>{i.measure} — from {i.from_period} <span className="text-slate">({i.affects.map((x) => ACTIVITY_LABEL[x]).join(", ")})</span></li>)}
      </ul>
      {canEdit && (
        <form className="mt-3 flex flex-wrap items-end gap-2" onSubmit={(e) => { e.preventDefault(); add.mutate(); }}>
          <Field label="New intervention"><input className={`${inputCls} w-64`} required value={iv.measure} onChange={(e) => setIv({ ...iv, measure: e.target.value })} placeholder="e.g. Condensate recovery" /></Field>
          <Field label="From (YYYY-MM)"><input className={`${inputCls} w-32`} required pattern="\d{4}-(0[1-9]|1[0-2])" value={iv.from_period} onChange={(e) => setIv({ ...iv, from_period: e.target.value })} /></Field>
          <Field label="Affects"><select className={`${inputCls} w-44`} value={iv.affects} onChange={(e) => setIv({ ...iv, affects: e.target.value })}>
            <option value="">Infer from description</option>{Object.entries(ACTIVITY_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></Field>
          <Button type="submit" busy={add.isPending}>Add</Button>
        </form>
      )}
      {add.error && <div className="mt-2"><ErrorState error={add.error} /></div>}
    </Card>
  );
}

function MembersCard({ isOwner, isDemo }: { isOwner: boolean; isDemo: boolean }) {
  const qc = useQueryClient();
  const m = useQuery({ queryKey: ["members"], queryFn: () => api<Members>("/v1/org/members") });
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("reviewer");
  const invite = useMutation({ mutationFn: () => post("/v1/org/invites", { email, role }), onSuccess: () => { setEmail(""); qc.invalidateQueries({ queryKey: ["members"] }); } });
  const change = useMutation({ mutationFn: (x: { id: string; role: Role }) => api(`/v1/org/members/${x.id}`, { method: "PUT", body: JSON.stringify({ role: x.role }) }), onSuccess: () => qc.invalidateQueries({ queryKey: ["members"] }) });
  return (
    <Card>
      <CardTitle hint="Buyers and lenders see reports but not raw bills; reviewers confirm data and sign proofs.">Users and roles</CardTitle>
      {m.isLoading ? <Loading /> : m.error ? <ErrorState error={m.error} /> : (
        <table className="w-full text-sm"><tbody>
          {m.data?.members.map((u) => (
            <tr key={u.user_id} className="border-t border-line">
              <td className="py-2">{u.display_name ?? u.email}<span className="block text-xs text-slate">{u.email}</span></td>
              <td className="py-2 text-right">{isOwner ? (
                <select aria-label={`Role of ${u.email}`} className={`${inputCls} h-8 w-32`} value={u.role} onChange={(e) => change.mutate({ id: u.user_id, role: e.target.value as Role })}>{ROLES.map((r) => <option key={r}>{r}</option>)}</select>
              ) : <Badge>{u.role}</Badge>}</td>
            </tr>
          ))}
          {m.data?.invites.map((i) => <tr key={i.id} className="border-t border-line"><td className="py-2 text-slate">{i.email} <span className="text-xs">(invited)</span></td><td className="py-2 text-right"><Badge>{i.role}</Badge></td></tr>)}
        </tbody></table>
      )}
      {change.error && <ErrorState error={change.error} />}
      {isOwner && !isDemo && (
        <form className="mt-3 flex flex-wrap items-end gap-2" onSubmit={(e) => { e.preventDefault(); invite.mutate(); }}>
          <Field label="Invite by email" hint="They join with this role the first time they sign in."><input type="email" className={`${inputCls} w-64`} required value={email} onChange={(e) => setEmail(e.target.value)} /></Field>
          <Field label="Role"><select className={`${inputCls} w-32`} value={role} onChange={(e) => setRole(e.target.value as Role)}>{ROLES.map((r) => <option key={r}>{r}</option>)}</select></Field>
          <Button type="submit" busy={invite.isPending}>Invite</Button>
        </form>
      )}
      {isDemo && <p className="mt-2 text-xs text-slate">Invitations are disabled in the demo sandbox.</p>}
      {invite.error && <div className="mt-2"><ErrorState error={invite.error} /></div>}
    </Card>
  );
}

function FactorsCard() {
  const f = useQuery({ queryKey: ["factors"], queryFn: () => api<Factor[]>("/v1/factors") });
  const [all, setAll] = useState(false);
  const rows = (f.data ?? []).filter((x) => all || x.activity !== "grid_electricity" || (x.year ?? 0) >= 2020 || x.kind === "official");
  return (
    <Card>
      <CardTitle hint="Versioned and immutable: a changed value gets a new version. Official national factors are entered by C-Trace platform admins with their publication link."
        action={<button type="button" className="text-sm text-teal hover:underline" onClick={() => setAll((v) => !v)}>{all ? "Recent grid years only" : "Show all years"}</button>}>Emission factors</CardTitle>
      {f.isLoading ? <Loading /> : (
        <div className="overflow-x-auto">
          <table className="tabular w-full min-w-[640px] text-sm">
            <thead className="text-left text-xs text-slate"><tr><th className="py-1">ID</th><th>Source</th><th className="text-right">kgCO₂e / unit</th><th>Kind</th><th /></tr></thead>
            <tbody>{rows.map((x) => (
              <tr key={x.id} className="border-t border-line"><td className="py-1.5 font-mono text-xs">{x.id}</td><td className="max-w-md truncate text-xs" title={x.source}>{x.source}</td>
                <td className="text-right">{x.kgco2e_per_unit} / {x.unit}</td><td><Badge tone={x.kind === "official" ? "teal" : "slate"}>{x.kind}</Badge></td>
                <td><a href={x.source_url} target="_blank" rel="noreferrer" className="text-teal" aria-label={`Source of ${x.id}`}><ExternalLink className="size-3.5" /></a></td></tr>
            ))}</tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

function DataCard({ factoryId, name, isOwner, canExport }: { factoryId: string; name: string; isOwner: boolean; canExport: boolean }) {
  const router = useRouter();
  const qc = useQueryClient();
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const del = useMutation({ mutationFn: () => post(`/v1/factories/${factoryId}/delete`, { confirm_name: confirm }), onSuccess: () => { qc.clear(); clearSession(); router.replace("/"); } });
  return (
    <Card>
      <CardTitle>Your data</CardTitle>
      {canExport && <Button variant="secondary" busy={busy} onClick={async () => { setBusy(true); try { await download(`/v1/factories/${factoryId}/export.zip`, "C-Trace_export.zip"); } finally { setBusy(false); } }}><Download className="size-4" aria-hidden /> Export everything (zip)</Button>}
      {isOwner && (
        <form className="mt-5 rounded-lg border border-low/30 p-4" onSubmit={(e) => { e.preventDefault(); del.mutate(); }}>
          <p className="text-sm font-medium text-low">Delete this factory and all its data</p>
          <p className="mt-1 text-xs text-slate">Bills, ledger, verification runs, reductions and reports are removed permanently. The audit log keeps a record that the deletion happened. This cannot be undone.</p>
          <Field label={`Type “${name}” to confirm`}><input className={`${inputCls} mt-1`} value={confirm} onChange={(e) => setConfirm(e.target.value)} /></Field>
          {del.error && <div className="mt-2"><ErrorState error={del.error instanceof ApiError ? del.error : new Error("Could not delete")} /></div>}
          <Button type="submit" variant="danger" className="mt-3" disabled={confirm !== name} busy={del.isPending}><Trash2 className="size-4" aria-hidden /> Delete permanently</Button>
        </form>
      )}
    </Card>
  );
}
