const TOKEN = "token";
export const getToken = () => localStorage.getItem(TOKEN);
export const setToken = (t: string | null) => (t ? localStorage.setItem(TOKEN, t) : localStorage.removeItem(TOKEN));

export class ApiError extends Error {
  constructor(public status: number, public detail: unknown) {
    super(typeof detail === "string" ? detail : (detail as { error?: string })?.error ?? `Request failed (${status})`);
  }
}

type Opts = { method?: string; body?: unknown; form?: FormData; params?: Record<string, unknown> };

export async function api<T = any>(path: string, opts: Opts = {}): Promise<T> {
  const url = new URL(path, window.location.origin);
  Object.entries(opts.params ?? {}).forEach(([k, v]) => v !== undefined && v !== null && v !== "" && url.searchParams.set(k, String(v)));
  const headers: Record<string, string> = {};
  const t = getToken();
  if (t) headers.authorization = `Bearer ${t}`;
  if (opts.body !== undefined) headers["content-type"] = "application/json";
  const res = await fetch(url, {
    method: opts.method ?? (opts.body !== undefined || opts.form ? "POST" : "GET"),
    headers,
    body: opts.form ?? (opts.body !== undefined ? JSON.stringify(opts.body) : undefined),
  });
  if (res.status === 401 && !path.includes("/auth/login") && !path.includes("/auth/sso")) {
    setToken(null);
    window.location.assign("/login");
  }
  const text = await res.text();
  const data = text ? (() => { try { return JSON.parse(text); } catch { return text; } })() : null;
  if (!res.ok) throw new ApiError(res.status, data && typeof data === "object" && "detail" in data ? (data as any).detail : data);
  return data as T;
}

export async function download(path: string, filename: string) {
  const res = await fetch(path, { headers: { authorization: `Bearer ${getToken()}` } });
  if (!res.ok) throw new ApiError(res.status, "Download failed");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(await res.blob());
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
}
