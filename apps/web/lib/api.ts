export const API_URL =
  process.env.NEXT_PUBLIC_API_URL ??
  "http://localhost:8000";

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/** Pull FastAPI's `detail` off an error body, falling back to a status message. */
export async function toApiError(
  response: Response,
): Promise<ApiError> {
  let detail: string | null = null;

  try {
    const body = await response.json();

    if (typeof body?.detail === "string") {
      detail = body.detail;
    } else if (Array.isArray(body?.detail)) {
      // Pydantic validation errors arrive as a list of issues.
      detail = body.detail
        .map((issue: { msg?: string }) => issue.msg)
        .filter(Boolean)
        .join(", ");
    }
  } catch {
    detail = null;
  }

  return new ApiError(
    response.status,
    detail ?? `Request failed with status ${response.status}`,
  );
}

/**
 * Merge caller headers over the JSON defaults.
 *
 * Returns `Headers` rather than a plain object so names compare
 * case-insensitively. A plain spread treats `authorization` and `Authorization`
 * as two distinct keys, which lets a caller-supplied value survive alongside
 * one the caller should not be able to influence.
 */
export function buildHeaders(init?: HeadersInit): Headers {
  const headers = new Headers(init);

  if (!headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  return headers;
}

/**
 * Send a request, throwing an ApiError on any non-2xx response.
 *
 * The single place response failure is turned into an error, so authenticated
 * and anonymous callers cannot drift apart in how they report one.
 */
export async function apiSend(
  path: string,
  options?: RequestInit,
): Promise<Response> {
  const response = await fetch(`${API_URL}${path}`, {
    ...options,
    headers: buildHeaders(options?.headers),
  });

  if (!response.ok) {
    throw await toApiError(response);
  }

  return response;
}

export async function apiJson<T>(
  path: string,
  options?: RequestInit,
): Promise<T> {
  const response = await apiSend(path, options);

  return response.json() as Promise<T>;
}

export async function apiPostJson<T>(
  path: string,
  body: unknown,
): Promise<T> {
  return apiJson<T>(path, { method: "POST", body: JSON.stringify(body) });
}

/**
 * Send a request whose response carries no body, such as a 204.
 *
 * Kept separate from the JSON helpers rather than returning a cast: a caller
 * that expects no body says so, so nothing has to pretend `undefined` is a `T`.
 */
export async function apiPostEmpty(
  path: string,
  body: unknown,
): Promise<void> {
  await apiSend(path, { method: "POST", body: JSON.stringify(body) });
}
