const API_PREFIX = "/api/v1";

const DEFAULT_SERVER_ORIGIN =
  "http://127.0.0.1:8000";


export type BimapApiErrorPayload = {
  readonly [key: string]: unknown;
};


export class BimapApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly payload: unknown = null,
    public readonly requestId: string | null = null,
    public readonly correlationId: string | null = null,
  ) {
    super(message);
    this.name = "BimapApiError";
  }
}


function normalizeApiPath(
  path: string,
): string {
  const normalized = path.trim();

  if (!normalized) {
    throw new TypeError(
      "BIMAP API path cannot be empty.",
    );
  }

  if (
    normalized.startsWith("http://") ||
    normalized.startsWith("https://") ||
    normalized.startsWith("//")
  ) {
    throw new TypeError(
      "BIMAP API path must be relative.",
    );
  }

  return normalized.startsWith("/")
    ? normalized
    : `/${normalized}`;
}


function resolveServerOrigin(): string {
  const raw =
    process.env.BIMAP_BACKEND_URL?.trim() ||
    DEFAULT_SERVER_ORIGIN;

  let url: URL;

  try {
    url = new URL(raw);
  } catch {
    throw new Error(
      "BIMAP_BACKEND_URL must be a valid absolute URL.",
    );
  }

  if (
    url.protocol !== "http:" &&
    url.protocol !== "https:"
  ) {
    throw new Error(
      "BIMAP_BACKEND_URL must use http or https.",
    );
  }

  if (url.username || url.password) {
    throw new Error(
      "BIMAP_BACKEND_URL must not contain credentials.",
    );
  }

  return url.origin;
}


function resolveRequestUrl(
  path: string,
): string {
  const target =
    `${API_PREFIX}${normalizeApiPath(path)}`;

  /*
   * Browser:
   *
   *   /api/v1/... -> Next.js rewrite -> FastAPI
   *
   * Server component / route handler:
   *
   *   http://backend:8000/api/v1/... -> FastAPI directly
   */
  if (typeof window !== "undefined") {
    return target;
  }

  return `${resolveServerOrigin()}${target}`;
}


async function readErrorPayload(
  response: Response,
): Promise<unknown> {
  const contentType =
    response.headers.get("content-type") ?? "";

  try {
    if (
      contentType
        .toLowerCase()
        .includes("application/json")
    ) {
      return await response.json();
    }

    const text = await response.text();

    return text || null;
  } catch {
    return null;
  }
}


export async function apiRequest<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const headers = new Headers(
    init.headers,
  );

  if (!headers.has("Accept")) {
    headers.set(
      "Accept",
      "application/json",
    );
  }

  const response = await fetch(
    resolveRequestUrl(path),
    {
      ...init,
      headers,

      // Customer/order/audit API data is operational state.
      // Callers may explicitly override this when a future endpoint is safe
      // to cache.
      cache: init.cache ?? "no-store",

      credentials:
        init.credentials ?? "same-origin",
    },
  );

  if (!response.ok) {
    const payload =
      await readErrorPayload(response);

    throw new BimapApiError(
      `BIMAP request failed with HTTP ${response.status}.`,
      response.status,
      payload,
      response.headers.get(
        "x-request-id",
      ),
      response.headers.get(
        "x-correlation-id",
      ),
    );
  }

  if (
    response.status === 204 ||
    response.headers.get(
      "content-length",
    ) === "0"
  ) {
    return undefined as T;
  }

  return (await response.json()) as T;
}


export async function apiJsonRequest<T>(
  path: string,
  options: {
    method:
      | "POST"
      | "PUT"
      | "PATCH"
      | "DELETE";
    body?: unknown;
    headers?: HeadersInit;
    signal?: AbortSignal;
  },
): Promise<T> {
  const headers = new Headers(
    options.headers,
  );

  if (
    options.body !== undefined &&
    !headers.has("Content-Type")
  ) {
    headers.set(
      "Content-Type",
      "application/json",
    );
  }

  return apiRequest<T>(
    path,
    {
      method: options.method,
      headers,
      signal: options.signal,
      body:
        options.body === undefined
          ? undefined
          : JSON.stringify(
              options.body,
            ),
    },
  );
}