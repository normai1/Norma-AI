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

export async function apiFetch<T>(
  path: string,
  options?: RequestInit,
): Promise<T> {
  const response = await fetch(
    `${API_URL}${path}`,
    {
      ...options,
      headers: {
        "Content-Type": "application/json",
        ...options?.headers,
      },
    },
  );

  if (!response.ok) {
    throw await toApiError(response);
  }

  // 204 carries no body, so parsing it would throw on an otherwise fine
  // request. Logout is the current example.
  if (response.status === 204) {
    return undefined as T;
  }

  return response.json() as Promise<T>;
}

export async function apiPost<T>(
  path: string,
  body: unknown,
): Promise<T> {
  return apiFetch<T>(path, {
    method: "POST",
    body: JSON.stringify(body),
  });
}
