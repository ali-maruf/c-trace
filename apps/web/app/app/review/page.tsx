"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Check, ChevronDown, ChevronUp, Lightbulb, Pencil, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { DocumentViewer, EvidencePanel } from "@/components/document-viewer";
import { Badge, Button, Card, CardTitle, Empty, ErrorState, Field, Loading, PageHeader, inputCls } from "@/components/ui";
import { api, post } from "@/lib/api";
import { ACTIVITY_LABEL, monthLabel, n } from "@/lib/format";
import { qk, useFactory, useMe } from "@/lib/hooks";
import type { ActivityRecord, Suggestion } from "@/lib/types";

const REVIEW_ROLES = ["owner", "manager", "reviewer"];
type Filter = "todo" | "all";

export default function ReviewPage() {
  const { id } = useFactory();
  const me = useMe();
  const qc = useQueryClient();
  const [filter, setFilter] = useState<Filter>("todo");
  const [cursor, setCursor] = useState(0);
  const [editing, setEditing] = useState(false);
  const [rejecting, setRejecting] = useState(false);
  const records = useQuery({ queryKey: qk.records(id ?? ""), queryFn: () => api<ActivityRecord[]>(`/v1/factories/${id}/activity-records`), enabled: !!id });
  const sugg = useQuery({ queryKey: qk.suggestions(id ?? ""), queryFn: () => api<Suggestion[]>(`/v1/factories/${id}/suggestions`), enabled: !!id });
  const canReview = REVIEW_ROLES.includes(me.data?.role ?? "");

  const list = useMemo(() => {
    const all = records.data ?? [];
    const flagged = (r: ActivityRecord) => (r.issues?.length ?? 0) > 0 || sugg.data?.some((s) => s.activity_record_id === r.id);
    const rows = filter === "todo" ? all.filter((r) => r.status === "extracted") : all;
    return [...rows].sort((a, b) => Number(flagged(b)) - Number(flagged(a)) || (a.period ?? "").localeCompare(b.period ?? ""));
  }, [records.data, sugg.data, filter]);
  const rec = list[Math.min(cursor, Math.max(list.length - 1, 0))];
  const suggestion = sugg.data?.find((s) => s.activity_record_id === rec?.id);

  const done = useCallback(() => {
    setEditing(false);
    setRejecting(false);
    qc.invalidateQueries();
  }, [qc]);
  const confirm = useMutation({ mutationFn: (r: ActivityRecord) => post(`/v1/activity-records/${r.id}/confirm`, { reason: "Checked against the original bill" }), onSuccess: done });
  const accept = useMutation({
    mutationFn: (s: Suggestion) => post(`/v1/activity-records/${s.activity_record_id}/correct`, { quantity: s.suggested_quantity, reason: `Accepted C-Trace suggestion: ${s.explanation}` }),
    onSuccess: done,
  });

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement || e.target instanceof HTMLSelectElement) return;
      if (e.key === "j") setCursor((c) => Math.min(c + 1, list.length - 1));
      else if (e.key === "k") setCursor((c) => Math.max(c - 1, 0));
      else if (e.key === "a" && rec && canReview && !suggestion) confirm.mutate(rec);
      else if (e.key === "e" && rec && canReview) setEditing(true);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [list.length, rec, canReview, suggestion, confirm]);

  if (records.error) return <ErrorState error={records.error} retry={() => records.refetch()} />;
  const todo = (records.data ?? []).filter((r) => r.status === "extracted").length;

  return (
    <div>
      <PageHeader
        title="Review queue"
        description={<>Check what C-Trace read against the bill. <kbd className="rounded border px-1 font-mono text-xs">A</kbd> confirm · <kbd className="rounded border px-1 font-mono text-xs">E</kbd> edit · <kbd className="rounded border px-1 font-mono text-xs">J</kbd>/<kbd className="rounded border px-1 font-mono text-xs">K</kbd> next / previous</>}
        action={
          <div className="flex rounded-md border border-line p-0.5 text-sm" role="group" aria-label="Filter">
            {(["todo", "all"] as Filter[]).map((f) => (
              <button key={f} type="button" onClick={() => { setFilter(f); setCursor(0); }} aria-pressed={filter === f}
                className={`rounded px-3 py-1 ${filter === f ? "bg-teal text-white" : "text-ink"}`}>{f === "todo" ? `To review (${todo})` : "All"}</button>
            ))}
          </div>
        }
      />
      {!canReview && me.data && <p className="mb-3 rounded-md bg-teal-light px-3 py-2 text-sm text-teal-dark">Your role ({me.data.role}) can view but not confirm or correct records.</p>}
      {records.isLoading ? <Loading /> : !rec ? (
        <Empty title="Nothing left to review">Every bill has been confirmed, corrected or rejected. Run verification next.</Empty>
      ) : (
        <div className="grid gap-4 lg:grid-cols-[260px_minmax(0,1fr)_minmax(0,1fr)]">
          <Card className="max-h-[70vh] overflow-y-auto p-2 sm:p-2">
            <ul aria-label="Records">
              {list.map((r, i) => {
                const flagged = (r.issues?.length ?? 0) > 0 || sugg.data?.some((s) => s.activity_record_id === r.id);
                return (
                  <li key={r.id}>
                    <button type="button" onClick={() => { setCursor(i); setEditing(false); setRejecting(false); }} aria-current={r.id === rec.id}
                      className={`flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm ${r.id === rec.id ? "bg-teal-light" : "hover:bg-slate-50"}`}>
                      {flagged ? <AlertTriangle className="size-3.5 shrink-0 text-medium" aria-label="Needs attention" /> : <span className="size-3.5 shrink-0" />}
                      <span className="min-w-0 flex-1 truncate">{r.doc_name}</span>
                      {r.status !== "extracted" && <Badge tone="teal">{r.status}</Badge>}
                    </button>
                  </li>
                );
              })}
            </ul>
          </Card>

          <div className="space-y-4">
            <Card>
              <CardTitle hint={`${rec.extraction_method === "rules" ? "Offline parser" : rec.extraction_method ?? ""} · confidence ${rec.extraction_confidence === null ? "—" : Math.round(rec.extraction_confidence * 100) + "%"}`}
                action={<div className="flex gap-1"><Button size="sm" variant="ghost" aria-label="Previous record" onClick={() => setCursor((c) => Math.max(c - 1, 0))}><ChevronUp className="size-4" /></Button><Button size="sm" variant="ghost" aria-label="Next record" onClick={() => setCursor((c) => Math.min(c + 1, list.length - 1))}><ChevronDown className="size-4" /></Button></div>}>
                {rec.activity ? ACTIVITY_LABEL[rec.activity] : "Unknown bill type"} · {rec.period ? monthLabel(rec.period) : "no period"}
              </CardTitle>
              <dl className="tabular grid grid-cols-2 gap-3 text-sm">
                <div><dt className="text-slate">Consumption</dt><dd className="text-lg font-semibold">{n(rec.quantity)} {rec.unit}</dd></div>
                <div><dt className="text-slate">Amount</dt><dd className="text-lg font-semibold">BDT {n(rec.amount_bdt, 2)}</dd></div>
                <div><dt className="text-slate">Bill no.</dt><dd>{rec.bill_no ?? "—"}</dd></div>
                <div><dt className="text-slate">Supplier</dt><dd>{rec.supplier ?? "—"}</dd></div>
              </dl>
              {rec.issues?.length > 0 && (
                <ul className="mt-3 space-y-1 rounded-md border border-medium/30 bg-medium-bg p-3 text-sm text-medium">
                  {rec.issues.map((i) => <li key={i} className="flex gap-2"><AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />{i}</li>)}
                </ul>
              )}
              {suggestion && (
                <div className="mt-3 rounded-md border border-teal/30 bg-teal-light p-3 text-sm">
                  <p className="flex items-center gap-2 font-medium text-teal-dark"><Lightbulb className="size-4" aria-hidden /> C-Trace suggests {n(suggestion.suggested_quantity)} {rec.unit}</p>
                  <p className="mt-1 text-slate">{suggestion.explanation} Nothing changes until you accept.</p>
                  {canReview && <Button className="mt-2" size="sm" busy={accept.isPending} onClick={() => accept.mutate(suggestion)}><Check className="size-4" aria-hidden /> Accept correction</Button>}
                </div>
              )}
              {(confirm.error || accept.error) && <div className="mt-3"><ErrorState error={confirm.error ?? accept.error} /></div>}
              {canReview && !editing && !rejecting && rec.status !== "confirmed" && (
                <div className="mt-4 flex flex-wrap gap-2">
                  {!suggestion && <Button busy={confirm.isPending} onClick={() => confirm.mutate(rec)} disabled={!rec.quantity || !rec.period}><Check className="size-4" aria-hidden /> Confirm (A)</Button>}
                  <Button variant="secondary" onClick={() => setEditing(true)}><Pencil className="size-4" aria-hidden /> Edit (E)</Button>
                  <Button variant="danger" onClick={() => setRejecting(true)}><X className="size-4" aria-hidden /> Reject</Button>
                </div>
              )}
              {editing && <EditForm rec={rec} onDone={done} onCancel={() => setEditing(false)} />}
              {rejecting && <RejectForm rec={rec} onDone={done} onCancel={() => setRejecting(false)} />}
            </Card>
            <Card><CardTitle>Evidence</CardTitle><EvidencePanel documentId={rec.document_id} /></Card>
          </div>
          <Card><CardTitle>Original bill</CardTitle><DocumentViewer documentId={rec.document_id} className="h-[640px]" /></Card>
        </div>
      )}
    </div>
  );
}

function EditForm({ rec, onDone, onCancel }: { rec: ActivityRecord; onDone: () => void; onCancel: () => void }) {
  const [quantity, setQuantity] = useState(String(rec.quantity ?? ""));
  const [period, setPeriod] = useState(rec.period ?? "");
  const [amount, setAmount] = useState(String(rec.amount_bdt ?? ""));
  const [reason, setReason] = useState("");
  const m = useMutation({
    mutationFn: () => {
      const changes: Record<string, unknown> = {};
      if (Number(quantity) !== rec.quantity) changes.quantity = Number(quantity);
      if (period !== rec.period) changes.period = period;
      if (amount && Number(amount) !== rec.amount_bdt) changes.amount_bdt = Number(amount);
      return post(`/v1/activity-records/${rec.id}/correct`, { ...changes, reason });
    },
    onSuccess: onDone,
  });
  return (
    <form className="mt-4 space-y-3 border-t border-line pt-4" onSubmit={(e) => { e.preventDefault(); m.mutate(); }}>
      <div className="grid gap-3 sm:grid-cols-3">
        <Field label={`Quantity (${rec.unit ?? ""})`}><input className={inputCls} inputMode="decimal" value={quantity} onChange={(e) => setQuantity(e.target.value)} required /></Field>
        <Field label="Period (YYYY-MM)"><input className={inputCls} value={period} onChange={(e) => setPeriod(e.target.value)} pattern="\d{4}-(0[1-9]|1[0-2])" required /></Field>
        <Field label="Amount (BDT)"><input className={inputCls} inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} /></Field>
      </div>
      <Field label="Reason (kept in the audit log)"><input className={inputCls} value={reason} onChange={(e) => setReason(e.target.value)} minLength={3} required placeholder="e.g. Bill shows 32,044; parser misread" /></Field>
      {m.error && <ErrorState error={m.error} />}
      <div className="flex gap-2"><Button type="submit" busy={m.isPending}>Save correction</Button><Button type="button" variant="ghost" onClick={onCancel}>Cancel</Button></div>
    </form>
  );
}

function RejectForm({ rec, onDone, onCancel }: { rec: ActivityRecord; onDone: () => void; onCancel: () => void }) {
  const [reason, setReason] = useState("");
  const m = useMutation({ mutationFn: () => post(`/v1/activity-records/${rec.id}/reject`, { reason }), onSuccess: onDone });
  return (
    <form className="mt-4 space-y-3 border-t border-line pt-4" onSubmit={(e) => { e.preventDefault(); m.mutate(); }}>
      <Field label="Why reject this bill? (kept in the audit log)" hint="A rejected bill is left out of the ledger; it can be confirmed again later.">
        <input className={inputCls} value={reason} onChange={(e) => setReason(e.target.value)} minLength={3} required placeholder="e.g. Duplicate of the supplier statement" />
      </Field>
      {m.error && <ErrorState error={m.error} />}
      <div className="flex gap-2"><Button type="submit" variant="danger" busy={m.isPending}>Reject bill</Button><Button type="button" variant="ghost" onClick={onCancel}>Cancel</Button></div>
    </form>
  );
}
