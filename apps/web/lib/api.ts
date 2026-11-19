import { clearSession, getSession } from "./session";

export class ApiError extends Error {
  constructor(public status: number, message: string, public requestId?: string) {
    super(message);
  }
}

function headers(extra?: HeadersInit): Headers {
  const h = new Headers(extra);
  const s = getSession();
  if (s) h.set("Authorization", `Bearer ${s.token}`);
  if (s?.orgId) h.set("X-C-Trace-Org", s.orgId);
  return h;
}

async function failure(res: Response): Promise<ApiError> {
  let detail = res.statusText;
  try {
    const body = await res.json();
    detail = typeof body.detail === "string" ? body.detail : Array.isArray(body.detail) ? body.detail.map((d: { msg: string }) => d.msg).join("; ") : detail;
  } catch {
    /* not JSON */
  }
  if (res.status === 401) clearSession();
  return new ApiError(res.status, detail, res.headers.get("x-request-id") ?? undefined);
}

/** Same-origin call to the C-Trace API (`/api/v1/...`). */
export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const h = headers(init.headers);
  if (init.body && typeof init.body === "string") h.set("Content-Type", "application/json");
  const res = await fetch(`/api${path}`, { ...init, headers: h });
  if (!res.ok) throw await failure(res);
  return (res.status === 204 ? undefined : await res.json()) as T;
}

export const post = <T,>(path: string, body?: unknown) => api<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

/** Authorised binary download (documents, PDFs) as an object URL. */
export async function blobUrl(path: string): Promise<{ url: string; type: string; filename?: string }> {
  const res = await fetch(`/api${path}`, { headers: headers() });
  if (!res.ok) throw await failure(res);
  const blob = await res.blob();
  const cd = res.headers.get("content-disposition") ?? "";
  const filename = /filename="?([^";]+)"?/.exec(cd)?.[1];
  return { url: URL.createObjectURL(blob), type: blob.type, filename };
}

export async function download(path: string, fallbackName: string): Promise<void> {
  const { url, filename } = await blobUrl(path);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename ?? fallbackName;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

/** Multipart upload with progress (fetch has no upload progress). Resolves with the JSON body. */
export function uploadWithProgress<T>(path: string, form: FormData, onProgress: (fraction: number) => void): Promise<T> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `/api${path}`);
    headers().forEach((v, k) => xhr.setRequestHeader(k, v));
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
    xhr.onload = () => {
      let body: { detail?: string } = {};
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        /* empty */
      }
      if (xhr.status >= 200 && xhr.status < 300) resolve(body as T);
      else reject(new ApiError(xhr.status, typeof body.detail === "string" ? body.detail : xhr.statusText));
    };
    xhr.onerror = () => reject(new ApiError(0, "Network error — check your connection"));
    xhr.send(form);
  });
}
