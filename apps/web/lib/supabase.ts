import { createClient, type SupabaseClient } from "@supabase/supabase-js";

// Supabase Auth (email one-time code). Configured with the project URL and its publishable (anon) key,
// both public by design. Absent → sign-in is disabled and the live demo is the way in.
let client: SupabaseClient | null | undefined;

export function supabase(): SupabaseClient | null {
  if (client !== undefined) return client;
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const key = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  client = url && key ? createClient(url, key, { auth: { persistSession: false } }) : null;
  return client;
}
