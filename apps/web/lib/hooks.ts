"use client";

import { useQuery } from "@tanstack/react-query";
import { useEffect, useState, useSyncExternalStore } from "react";
import { api } from "./api";
import { getSession, onSessionChange, type Session } from "./session";
import type { Factory, Me } from "./types";

export function useSession(): Session | null | undefined {
  // undefined until hydrated (sessionStorage is browser-only)
  const [hydrated, setHydrated] = useState(false);
  useEffect(() => setHydrated(true), []);
  const s = useSyncExternalStore(onSessionChange, () => JSON.stringify(getSession()), () => "null");
  return hydrated ? (JSON.parse(s) as Session | null) : undefined;
}

export function useMe() {
  const session = useSession();
  return useQuery({ queryKey: ["me", session?.token], queryFn: () => api<Me>("/v1/me"), enabled: !!session });
}

export function useFactories() {
  const session = useSession();
  return useQuery({ queryKey: ["factories", session?.orgId], queryFn: () => api<Factory[]>("/v1/factories"), enabled: !!session });
}

/** The factory the app is looking at: the session's, else the organisation's first. */
export function useFactory() {
  const session = useSession();
  const list = useFactories();
  const id = session?.factoryId ?? list.data?.[0]?.id;
  const detail = useQuery({ queryKey: ["factory", id], queryFn: () => api<Factory>(`/v1/factories/${id}`), enabled: !!id });
  return { id, factory: detail.data, isLoading: list.isLoading || (!!id && detail.isLoading), error: list.error ?? detail.error, noFactory: list.isSuccess && !list.data?.length };
}

export const qk = {
  summary: (f: string, from?: string, to?: string) => ["summary", f, from, to] as const,
  ledger: (f: string, period?: string, activity?: string) => ["ledger", f, period, activity] as const,
  records: (f: string) => ["records", f] as const,
  suggestions: (f: string) => ["suggestions", f] as const,
  verification: (f: string) => ["verification", f] as const,
  reductions: (f: string) => ["reductions", f] as const,
};
