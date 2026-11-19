"use client";

import { useQueryClient } from "@tanstack/react-query";
import { Camera, CheckCircle2, CircleAlert, Copy, FileUp, Loader2 } from "lucide-react";
import Link from "next/link";
import { useCallback, useRef, useState } from "react";
import { Button, Card, CardTitle, PageHeader } from "@/components/ui";
import { ApiError, uploadWithProgress } from "@/lib/api";
import { useFactory } from "@/lib/hooks";
import type { UploadResult } from "@/lib/types";

const ACCEPT = "application/pdf,image/jpeg,image/png";
const MAX_BYTES = 4 * 1024 * 1024; // one file per request, under the host's 4.5 MB request limit

type Item = { key: string; file: File; progress: number; state: "queued" | "uploading" | "done" | "duplicate" | "error"; result?: UploadResult; error?: string };

export default function UploadPage() {
  const { id } = useFactory();
  const qc = useQueryClient();
  const [items, setItems] = useState<Item[]>([]);
  const [drag, setDrag] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const camRef = useRef<HTMLInputElement>(null);
  const update = (key: string, patch: Partial<Item>) => setItems((xs) => xs.map((x) => (x.key === key ? { ...x, ...patch } : x)));

  const send = useCallback(async (files: File[]) => {
    if (!id) return;
    const added = files.map((file) => ({ key: `${file.name}-${file.size}-${crypto.randomUUID()}`, file, progress: 0, state: "queued" as const }));
    setItems((xs) => [...added, ...xs]);
    for (const it of added) {
      if (it.file.size > MAX_BYTES) { update(it.key, { state: "error", error: "Larger than 4 MB — photograph the bill at a lower resolution or upload the PDF" }); continue; }
      if (!ACCEPT.split(",").includes(it.file.type)) { update(it.key, { state: "error", error: "C-Trace reads PDF, JPEG and PNG bills" }); continue; }
      update(it.key, { state: "uploading" });
      const form = new FormData();
      form.append("files", it.file);
      try {
        const [r] = await uploadWithProgress<UploadResult[]>(`/v1/factories/${id}/documents`, form, (p) => update(it.key, { progress: p }));
        update(it.key, { state: r?.duplicate ? "duplicate" : "done", result: r, progress: 1 });
      } catch (e) {
        update(it.key, { state: "error", error: e instanceof ApiError ? e.message : "Upload failed" });
      }
    }
    qc.invalidateQueries();
  }, [id, qc]);

  return (
    <div>
      <PageHeader title="Upload bills" description="Gas and electricity bills, diesel receipts: PDF, scan or phone photo, English or Bangla. Uploading the same file twice is safe — C-Trace recognises it by its fingerprint (SHA-256)." />
      <div className="grid gap-4 lg:grid-cols-[1.2fr_1fr]">
        <div
          onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
          onDragLeave={() => setDrag(false)}
          onDrop={(e) => { e.preventDefault(); setDrag(false); send(Array.from(e.dataTransfer.files)); }}
          className={`flex min-h-[260px] flex-col items-center justify-center rounded-xl border-2 border-dashed p-6 text-center ${drag ? "border-teal bg-teal-light" : "border-line"}`}
        >
          <FileUp className="size-8 text-teal" aria-hidden />
          <p className="mt-3 font-medium">Drop bills here</p>
          <p className="mt-1 text-sm text-slate">PDF, JPEG or PNG, up to 4 MB each</p>
          <div className="mt-4 flex flex-wrap justify-center gap-2">
            <Button onClick={() => fileRef.current?.click()}><FileUp className="size-4" aria-hidden /> Choose files</Button>
            <Button variant="secondary" onClick={() => camRef.current?.click()}><Camera className="size-4" aria-hidden /> Take a photo</Button>
          </div>
          <input ref={fileRef} type="file" multiple accept={ACCEPT} className="hidden" aria-label="Choose bill files" onChange={(e) => { send(Array.from(e.target.files ?? [])); e.target.value = ""; }} />
          <input ref={camRef} type="file" accept="image/*" capture="environment" className="hidden" aria-label="Photograph a bill" onChange={(e) => { send(Array.from(e.target.files ?? [])); e.target.value = ""; }} />
        </div>
        <Card>
          <CardTitle hint="Results appear as each bill is read.">This session</CardTitle>
          {items.length === 0 ? <p className="text-sm text-slate">Nothing uploaded yet.</p> : (
            <ul className="space-y-2" aria-live="polite">
              {items.map((it) => (
                <li key={it.key} className="rounded-lg border border-line p-3 text-sm">
                  <div className="flex items-center gap-2">
                    {it.state === "uploading" || it.state === "queued" ? <Loader2 className="size-4 animate-spin text-teal" aria-hidden />
                      : it.state === "done" ? <CheckCircle2 className="size-4 text-teal" aria-hidden />
                      : it.state === "duplicate" ? <Copy className="size-4 text-slate" aria-hidden /> : <CircleAlert className="size-4 text-low" aria-hidden />}
                    <span className="min-w-0 flex-1 truncate font-medium">{it.file.name}</span>
                  </div>
                  {it.state === "uploading" && (
                    <div className="mt-2 h-1.5 rounded bg-slate-100" role="progressbar" aria-valuenow={Math.round(it.progress * 100)} aria-valuemin={0} aria-valuemax={100} aria-label={`Uploading ${it.file.name}`}>
                      <div className="h-1.5 rounded bg-teal" style={{ width: `${Math.round(it.progress * 100)}%` }} />
                    </div>
                  )}
                  {it.state === "duplicate" && <p className="mt-1 text-slate">{it.result?.message}</p>}
                  {it.state === "error" && <p className="mt-1 text-low">{it.error}</p>}
                  {it.state === "done" && (
                    <p className="mt-1 text-slate">
                      {it.result?.job?.status === "done"
                        ? (it.result.job.result?.issues?.length ? <span className="text-medium">Read, needs a check: {it.result.job.result.issues[0]}</span> : "Read — ready for review")
                        : it.result?.job?.status === "failed" ? <span className="text-low">Could not read: {it.result.job.last_error?.split("\n")[0]}</span> : "Queued for reading"}
                      {" "}<Link className="text-teal hover:underline" href="/app/review">Review →</Link>
                    </p>
                  )}
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
      <ProductionUpload factoryId={id} />
    </div>
  );
}

function ProductionUpload({ factoryId }: { factoryId?: string }) {
  const qc = useQueryClient();
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  return (
    <Card className="mt-4">
      <CardTitle hint="A CSV with columns period (YYYY-MM) and pieces. Needed for intensity, verification and reduction proofs.">Production records</CardTitle>
      <label className="inline-flex cursor-pointer items-center gap-2 text-sm font-medium text-teal hover:underline">
        <input type="file" accept=".csv,text/csv" className="sr-only" disabled={busy || !factoryId} onChange={async (e) => {
          const f = e.target.files?.[0];
          e.target.value = "";
          if (!f || !factoryId) return;
          setBusy(true);
          const form = new FormData();
          form.append("file", f);
          try {
            const r = await uploadWithProgress<{ months: number }>(`/v1/factories/${factoryId}/production/csv`, form, () => undefined);
            setMsg({ ok: true, text: `Saved ${r.months} months of production` });
            qc.invalidateQueries();
          } catch (err) {
            setMsg({ ok: false, text: err instanceof Error ? err.message : "Upload failed" });
          } finally {
            setBusy(false);
          }
        }} />
        {busy ? "Uploading…" : "Upload production CSV"}
      </label>
      {msg && <p role="status" className={`mt-2 text-sm ${msg.ok ? "text-teal" : "text-low"}`}>{msg.text}</p>}
    </Card>
  );
}
