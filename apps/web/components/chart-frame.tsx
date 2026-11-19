"use client";

import { useState } from "react";

/** Wraps a chart with an accessible label and an equivalent data table (for screen readers and for checking numbers). */
export function ChartFrame({ label, table, children, height = 300 }: { label: string; table: { head: string[]; rows: (string | number)[][] }; children: React.ReactNode; height?: number }) {
  const [asTable, setAsTable] = useState(false);
  return (
    <figure aria-label={label}>
      <div className="mb-2 flex justify-end">
        <button type="button" className="text-xs text-teal hover:underline" onClick={() => setAsTable((v) => !v)} aria-pressed={asTable}>
          {asTable ? "View as chart" : "View as table"}
        </button>
      </div>
      {asTable ? (
        <div className="max-h-[360px] overflow-auto">
          <table className="tabular w-full text-sm">
            <thead className="sticky top-0 bg-white text-left text-slate">
              <tr>{table.head.map((h) => <th key={h} className="border-b border-line px-2 py-1.5 font-medium">{h}</th>)}</tr>
            </thead>
            <tbody>
              {table.rows.map((r, i) => (
                <tr key={i} className="odd:bg-teal-light/40">{r.map((c, j) => <td key={j} className="px-2 py-1">{c}</td>)}</tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div role="img" aria-label={label} style={{ height }}>{children}</div>
      )}
    </figure>
  );
}
