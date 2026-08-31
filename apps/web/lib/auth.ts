import {
  API_URL,
  ApiError,
  apiPostEmpty,
  apiPostJson,
  buildHeaders,
  toApiError,
} from "./api";

export interface AuthUser {
  id: string;
  email: string;
  full_name: string | null;
  avatar_url: string | null;
  is_active: boolean;
  created_at: string;
}

export interface AuthResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
  user: AuthUser;
}

const ACCESS_TOKEN_KEY = "norma.access_token";
const REFRESH_TOKEN_KEY = "norma.refresh_token";

// Tokens live in localStorage because the API is a separate origin and takes
// bearer credentials. That is readable by any script on the page, so it trades
// XSS exposure for cross-origin simplicity; revisit with httpOnly cookies when
// the production domains are settled.
export function getAccessToken(): string | null {
  if (typeof window === "undefined") {
    return null;
  }

  return window.localStorage.getItem(ACCESS_TOKEN_KEY);
}

export function getRefreshToken(): string | null {
  if (typeof window === "undefined") {
    return null;
  }

  return window.localStorage.getItem(REFRESH_TOKEN_KEY);
}

export function storeTokens(auth: AuthResponse): void {
  if (typeof window === "undefined") {
    return;
  }

  window.localStorage.setItem(ACCESS_TOKEN_KEY, auth.access_token);
  window.localStorage.setItem(REFRESH_TOKEN_KEY, auth.refresh_token);
}

export function clearTokens(): void {
  if (typeof window === "undefined") {
    return;
  }

  window.localStorage.removeItem(ACCESS_TOKEN_KEY);
  window.localStorage.removeItem(REFRESH_TOKEN_KEY);
}

export async function register(input: {
  email: string;
  password: string;
  fullName?: string;
}): Promise<AuthResponse> {
  const auth = await apiPostJson<AuthResponse>("/api/v1/auth/register", {
    email: input.email,
    password: input.password,
    full_name: input.fullName?.trim() || null,
  });

  storeTokens(auth);

  return auth;
}

export async function login(input: {
  email: string;
  password: string;
}): Promise<AuthResponse> {
  const auth = await apiPostJson<AuthResponse>("/api/v1/auth/login", input);

  storeTokens(auth);

  return auth;
}

/** Revoke the stored session server-side, then clear it locally regardless. */
export async function logout(): Promise<void> {
  const refreshToken = getRefreshToken();

  if (refreshToken) {
    try {
      await apiPostEmpty("/api/v1/auth/logout", {
        refresh_token: refreshToken,
      });
    } catch {
      // A failed revoke must not strand the user in a signed-in UI.
    }
  }

  clearTokens();
}

async function refreshSession(): Promise<string | null> {
  const refreshToken = getRefreshToken();

  if (!refreshToken) {
    return null;
  }

  try {
    const auth = await apiPostJson<AuthResponse>("/api/v1/auth/refresh", {
      refresh_token: refreshToken,
    });

    storeTokens(auth);

    return auth.access_token;
  } catch {
    clearTokens();

    return null;
  }
}

/** Fetch with the access token, retrying once through refresh on a 401. */
export async function authorizedFetch(
  path: string,
  options?: RequestInit,
): Promise<Response> {
  const send = (token: string | null) => {
    const headers = buildHeaders(options?.headers, options?.body);

    // set() rather than a spread: Headers normalizes names, so a caller
    // passing `authorization` in any casing is replaced rather than joined.
    if (token) {
      headers.set("Authorization", `Bearer ${token}`);
    }

    return fetch(`${API_URL}${path}`, { ...options, headers });
  };

  let response = await send(getAccessToken());

  if (response.status === 401) {
    const refreshed = await refreshSession();

    if (refreshed) {
      response = await send(refreshed);
    }
  }

  return response;
}

/** Send an authenticated request, throwing an ApiError on a non-2xx response. */
export async function authorizedSend(
  path: string,
  options?: RequestInit,
): Promise<Response> {
  const response = await authorizedFetch(path, options);

  if (!response.ok) {
    throw await toApiError(response);
  }

  return response;
}

export async function authorizedJson<T>(
  path: string,
  options?: RequestInit,
): Promise<T> {
  const response = await authorizedSend(path, options);

  return response.json() as Promise<T>;
}

/** Authenticated request whose response carries no body, such as a 204. */
export async function authorizedEmpty(
  path: string,
  options?: RequestInit,
): Promise<void> {
  await authorizedSend(path, options);
}


/** Return the signed-in user, or null when there is no usable session. */
export async function fetchCurrentUser(): Promise<AuthUser | null> {
  if (!getAccessToken()) {
    return null;
  }

  const response = await authorizedFetch("/api/v1/auth/me");

  if (response.status === 401) {
    clearTokens();

    return null;
  }

  if (!response.ok) {
    throw await toApiError(response);
  }

  return response.json() as Promise<AuthUser>;
}

/** Update the signed-in user's own name and/or avatar. */
export async function updateProfile(input: {
  fullName: string;
  avatarUrl: string;
}): Promise<AuthUser> {
  return authorizedJson<AuthUser>("/api/v1/auth/me", {
    method: "PATCH",
    body: JSON.stringify({
      full_name: input.fullName.trim() || null,
      avatar_url: input.avatarUrl.trim() || null,
    }),
  });
}

/**
 * Change the signed-in user's own password. The backend revokes every
 * session and returns a fresh pair, so this stores the new tokens itself -
 * a caller that forgot to would silently sign the user out on their next
 * request.
 */
export async function changePassword(input: {
  currentPassword: string;
  newPassword: string;
}): Promise<AuthResponse> {
  const auth = await authorizedJson<AuthResponse>("/api/v1/auth/me/password", {
    method: "POST",
    body: JSON.stringify({
      current_password: input.currentPassword,
      new_password: input.newPassword,
    }),
  });

  storeTokens(auth);

  return auth;
}

export { ApiError };
