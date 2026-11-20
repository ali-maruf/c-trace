"use client";

import { useQuery } from "@tanstack/react-query";
import { useEffect } from "react";
import { api, blobUrl } from "@/lib/api";
import { shortHash } from "@/lib/format";
import type { DocumentDetail } from "@/lib/types";
import { ErrorState, Skeleton } from "./ui";

/** The original bill, fetched with the user's token (documents are never public URLs). */
export function DocumentViewer({ documentId, className = "h-[520px]" }: { documentId: string; className?: string }) {
  const doc = useQuery({ queryKey: ["document", documentId], queryFn: () => api<DocumentDetail>(`/v1/documents/${documentId}`) });
  const file = useQuery({ queryKey: ["document-file", documentId], queryFn: () => blobUrl(`/v1/documents/${documentId}/file`), staleTime: Infinity, retry: false });
  useEffect(() => () => { if (file.data) URL.revokeObjectURL(file.data.url); }, [file.data]);
  if (file.error) return <ErrorState error={file.error} />;
  if (!file.data || !doc.data) return <Skeleton className={className} />;
  return (
    <div>
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2 text-xs text-slate">
        <span className="font-medium text-ink">{doc.data.file_name}</span>
        <span className="font-mono" title={doc.data.sha256}>SHA-256 {shortHash(doc.data.sha256)}</span>
      </div>
      {file.data.type.startsWith("image/") ? (
        // eslint-disable-next-line @next/next/no-img-element -- authorised blob URL, not a static asset
        <img src={file.data.url} alt={`Original document ${doc.data.file_name}`} className={`w-full rounded-lg border border-line object-contain ${className}`} />
      ) : (
        <iframe src={file.data.url} title={`Original document ${doc.data.file_name}`} className={`w-full rounded-lg border border-line ${className}`} />
      )}
    </div>
  );
}

const FIELD_LABEL: Record<string, string> = {
  bill_no: "Bill no.", account_no: "Account no.", supplier: "Supplier", period: "Billing period", billing_period: "Billing period",
  quantity: "Consumption", consumption_quantity: "Consumption", consumption_unit: "Unit", amount_bdt: "Amount (BDT)", document_type: "Bill type", language: "Language",
};

/** Each extracted field next to the verbatim text it was read from; the value itself is highlighted. */
export function EvidencePanel({ documentId, highlight }: { documentId: string; highlight?: string[] }) {
  const doc = useQuery({ queryKey: ["document", documentId], queryFn: () => api<DocumentDetail>(`/v1/documents/${documentId}`) });
  if (!doc.data) return <Skeleton className="h-40" />;
  const ext = doc.data.extractions.at(-1);
  if (!ext) return <p className="text-sm text-slate">Not read yet.</p>;
  const rows = Object.entries(ext.field_evidence).filter(([k]) => k !== "implied_tariff_bdt_per_unit");
  return (
    <div>
      <p className="mb-2 text-xs text-slate">
        Read by <span className="font-medium text-ink">{ext.method === "llm" ? `an AI model (${ext.model})` : "the offline parser"}</span>
        {ext.prompt_version && <> · prompt {ext.prompt_version}</>} · confidence {ext.confidence === null ? "—" : `${Math.round(ext.confidence * 100)}%`}
      </p>
      <dl className="divide-y divide-line rounded-lg border border-line text-sm">
        {rows.map(([k, v]) => {
          const text = typeof v === "string" ? v : v.text;
          const conf = typeof v === "string" ? null : v.confidence;
          return (
            <div key={k} className="grid grid-cols-[120px_1fr] gap-2 px-3 py-2">
              <dt className="text-slate">{FIELD_LABEL[k] ?? k}</dt>
              <dd>
                <q className="font-mono text-xs not-italic text-ink"><Highlight text={text} terms={highlight} /></q>
                {conf !== null && conf < 0.7 && <span className="ml-2 text-xs text-medium">low confidence</span>}
              </dd>
            </div>
          );
        })}
      </dl>
    </div>
  );
}

function Highlight({ text, terms }: { text: string; terms?: string[] }) {
  // highlight the number after the label (the value C-Trace used), plus any explicit terms
  const m = /([\d০-৯][\d০-৯,]*(?:\.[\d০-৯]+)?)/.exec(text);
  const target = terms?.find((tm) => tm && text.includes(tm)) ?? m?.[1];
  if (!target) return <>{text}</>;
  const i = text.indexOf(target);
  return <>{text.slice(0, i)}<mark>{target}</mark>{text.slice(i + target.length)}</>;
}
