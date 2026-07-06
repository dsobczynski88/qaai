/**
 * Detect the root path based on the current URL.
 *
 * Examples:
 * - JupyterHub: https://aihub-ohio.aws.baxter.com/user/john/proxy/8000/ → /user/john/proxy/8000
 * - Local: http://localhost:8000/ → ''
 * - VSCode: https://aihub-ohio.aws.baxter.com/user/john/vscode/proxy/8000/ → /user/john/vscode/proxy/8000
 *
 * Ported verbatim from the original static/script.js — every request MUST stay
 * prefixed so the app works locally and behind the JupyterHub / ALB proxy.
 */
export function detectRootPath(): string {
  const pathname = window.location.pathname;

  const jupyterHubMatch = pathname.match(
    /^(\/user\/[^/]+\/(vscode\/)?proxy\/\d+)(\/|$)/,
  );
  if (jupyterHubMatch) {
    return jupyterHubMatch[1];
  }
  return "";
}

export const ROOT_PATH = detectRootPath();

// eslint-disable-next-line no-console
console.log("QAAI detected root path:", ROOT_PATH || "(local mode)");

/** Prefix an /api path with the detected proxy root. */
export function apiUrl(path: string): string {
  return ROOT_PATH + path;
}

/**
 * Auth-header injection seam. In the ALB + OIDC model the edge authenticates and
 * the browser sends the auth cookie automatically (same-origin), so no header is
 * needed here. If the identity provider is later swapped for Cognito (in-app JWT),
 * return `{ Authorization: \`Bearer ${token}\` }` from here — the single place all
 * requests flow through.
 */
export function authHeaders(): Record<string, string> {
  return {};
}

/** fetch() that prefixes ROOT_PATH and injects auth headers. */
export function apiFetch(path: string, opts: RequestInit = {}): Promise<Response> {
  const headers = { ...authHeaders(), ...(opts.headers as Record<string, string>) };
  return fetch(apiUrl(path), { ...opts, headers });
}

/** Uniform error extraction from a failed Response → "status: detail". */
export async function parseErr(resp: Response): Promise<string> {
  const err = await resp.json().catch(() => ({ detail: resp.statusText }));
  return `${resp.status}: ${err.detail || JSON.stringify(err)}`;
}
