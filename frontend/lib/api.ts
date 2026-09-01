const API_BASE = process.env.NEXT_PUBLIC_BIMAP_API_URL ?? "";

export class BimapApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
  ) {
    super(message);
    this.name = "BimapApiError";
  }
}

export async function apiRequest<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      Accept: "application/json",
      ...init?.headers,
    },
  });

  if (!response.ok) {
    throw new BimapApiError(
      `BIMAP request failed (${response.status}).`,
      response.status,
    );
  }

  return (await response.json()) as T;
}

/*
  SLAI integration boundary
  -------------------------
  The browser must not import SLAI Python modules directly.

  Later, BIMAP API routes can expose selected backend capabilities backed by
  SLAI/src/functions (for example authentication, search, storage and
  rate-limiting). The frontend consumes those capabilities through this HTTP
  client rather than duplicating SLAI business logic in TypeScript.
*/
