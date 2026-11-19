import { AlertTriangle, Loader2 } from "lucide-react";
import Link from "next/link";
import { cn } from "@/lib/cn";
import type { Confidence } from "@/lib/types";

type ButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "ghost" | "danger"; busy?: boolean; size?: "sm" | "md" };

export function Button({ variant = "primary", size = "md", busy, className, children, disabled, ...rest }: ButtonProps) {
  return (
    <button
      {...rest}
      disabled={disabled || busy}
      className={cn(
        "inline-flex items-center justify-center gap-2 rounded-md font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-60",
        size === "sm" ? "h-8 px-3 text-sm" : "h-10 px-4 text-sm",
        variant === "primary" && "bg-teal text-white hover:bg-teal-dark",
        variant === "secondary" && "border border-line bg-white text-ink hover:bg-teal-light",
        variant === "ghost" && "text-teal hover:bg-teal-light",
        variant === "danger" && "border border-low/40 bg-white text-low hover:bg-low-bg",
        className,
      )}
    >
      {busy && <Loader2 className="size-4 animate-spin" aria-hidden />}
      {children}
    </button>
  );
}

export function ButtonLink({ href, children, variant = "primary", className }: { href: string; children: React.ReactNode; variant?: "primary" | "secondary"; className?: string }) {
  return (
    <Link
      href={href}
      className={cn(
        "inline-flex h-10 items-center justify-center gap-2 rounded-md px-4 text-sm font-medium",
        variant === "primary" ? "bg-teal text-white hover:bg-teal-dark" : "border border-line bg-white hover:bg-teal-light",
        className,
      )}
    >
      {children}
    </Link>
  );
}

export function Card({ className, children, ...rest }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div {...rest} className={cn("rounded-xl border border-line bg-white p-4 shadow-[0_1px_2px_rgba(15,23,42,0.04)] sm:p-5", className)}>
      {children}
    </div>
  );
}

export function CardTitle({ children, hint, action }: { children: React.ReactNode; hint?: React.ReactNode; action?: React.ReactNode }) {
  return (
    <div className="mb-3 flex flex-wrap items-start justify-between gap-2">
      <div>
        <h2 className="text-base font-semibold text-ink">{children}</h2>
        {hint && <p className="mt-0.5 text-sm text-slate">{hint}</p>}
      </div>
      {action}
    </div>
  );
}

export function Stat({ label, value, sub, tone = "default" }: { label: string; value: React.ReactNode; sub?: React.ReactNode; tone?: "default" | "teal" }) {
  return (
    <Card className="min-h-[104px]">
      <p className="text-sm text-slate">{label}</p>
      <p className={cn("tabular mt-1 text-2xl font-semibold tracking-tight", tone === "teal" ? "text-teal" : "text-ink")}>{value}</p>
      {sub && <p className="mt-1 text-xs text-slate">{sub}</p>}
    </Card>
  );
}

const CONF: Record<Confidence, string> = {
  High: "bg-high-bg text-teal-dark border-teal/30",
  Medium: "bg-medium-bg text-medium border-medium/30",
  Low: "bg-low-bg text-low border-low/30",
};

export function ConfidencePill({ value, children, onClick, selected }: { value: Confidence; children?: React.ReactNode; onClick?: () => void; selected?: boolean }) {
  const cls = cn("tabular inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold", CONF[value], selected && "ring-2 ring-ink ring-offset-1");
  return onClick ? (
    <button type="button" onClick={onClick} className={cls} aria-pressed={selected} aria-label={`${children ?? ""} ${value} confidence`}>
      {children ?? value}
    </button>
  ) : (
    <span className={cls}>{children ?? value}</span>
  );
}

export function Badge({ tone = "slate", children }: { tone?: "slate" | "teal" | "low" | "medium"; children: React.ReactNode }) {
  const map = { slate: "bg-slate-100 text-slate", teal: "bg-teal-light text-teal-dark", low: "bg-low-bg text-low", medium: "bg-medium-bg text-medium" };
  return <span className={cn("inline-flex items-center rounded px-1.5 py-0.5 text-xs font-medium", map[tone])}>{children}</span>;
}

export function Loading({ label = "Loading" }: { label?: string }) {
  return (
    <div role="status" className="flex items-center gap-2 py-10 text-sm text-slate">
      <Loader2 className="size-4 animate-spin" aria-hidden /> {label}…
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div aria-hidden className={cn("animate-pulse rounded-md bg-slate-100", className)} />;
}

export function ErrorState({ error, retry }: { error: unknown; retry?: () => void }) {
  const msg = error instanceof Error ? error.message : "Something went wrong";
  return (
    <div role="alert" className="flex flex-wrap items-center gap-3 rounded-lg border border-low/30 bg-low-bg px-4 py-3 text-sm text-low">
      <AlertTriangle className="size-4" aria-hidden />
      <span className="flex-1">{msg}</span>
      {retry && (
        <Button size="sm" variant="secondary" onClick={retry}>
          Try again
        </Button>
      )}
    </div>
  );
}

export function Empty({ title, children, action }: { title: string; children?: React.ReactNode; action?: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-dashed border-line px-6 py-10 text-center">
      <p className="font-medium text-ink">{title}</p>
      {children && <div className="mx-auto mt-1 max-w-md text-sm text-slate">{children}</div>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function PageHeader({ title, description, action }: { title: string; description?: React.ReactNode; action?: React.ReactNode }) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-ink sm:text-2xl">{title}</h1>
        {description && <p className="mt-1 max-w-3xl text-sm text-slate">{description}</p>}
      </div>
      {action}
    </div>
  );
}

export function Field({ label, children, hint }: { label: string; children: React.ReactNode; hint?: string }) {
  return (
    <label className="block text-sm">
      <span className="font-medium text-ink">{label}</span>
      <div className="mt-1">{children}</div>
      {hint && <span className="mt-1 block text-xs text-slate">{hint}</span>}
    </label>
  );
}

export const inputCls = "h-10 w-full rounded-md border border-line bg-white px-3 text-sm text-ink placeholder:text-slate/70 focus:border-teal focus:outline-none";
