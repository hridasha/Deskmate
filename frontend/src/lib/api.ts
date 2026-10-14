export const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8010";

let memoryId: string | null = null;

/** Read a saved value under its current name, falling back to the name this app used before it was renamed (and moving it across). */
function readSaved(store: Storage, key: string, oldKey: string): string | null {
  const now = store.getItem(key);
  if (now !== null) return now;
  const old = store.getItem(oldKey);
  if (old !== null) {
    store.setItem(key, old);
    store.removeItem(oldKey);
  }
  return old;
}

/** An anonymous id for this browser. The server uses it to keep each person's chats, notes and memory separate. */
export function getUserId(): string {
  try {
    let id = readSaved(localStorage, "deskmates-user", "castcall-user");
    if (!id) {
      id = crypto.randomUUID();
      localStorage.setItem("deskmates-user", id);
    }
    return id;
  } catch {
    memoryId ??= crypto.randomUUID(); // storage blocked: a fresh id for this page load
    return memoryId;
  }
}

export function getAdminToken(): string {
  try {
    return readSaved(sessionStorage, "deskmates-admin", "castcall-admin") ?? "";
  } catch {
    return "";
  }
}

export function setAdminToken(token: string) {
  try {
    sessionStorage.setItem("deskmates-admin", token);
  } catch {
    /* ignore */
  }
}

export function getAuthToken(): string {
  try {
    return readSaved(localStorage, "deskmates-auth", "castcall-auth") ?? "";
  } catch {
    return "";
  }
}

export function setAuthToken(token: string) {
  try {
    localStorage.setItem("deskmates-auth", token);
  } catch {
    /* ignore */
  }
}

export function clearAuthToken() {
  try {
    localStorage.removeItem("deskmates-auth");
  } catch {
    /* ignore */
  }
}

/** fetch() against the backend, with the user id (and admin token, if set) attached. */
export function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  headers.set("X-User-Id", getUserId());
  const token = getAdminToken();
  if (token) headers.set("X-Admin-Token", token);
  const auth = getAuthToken();
  if (auth) headers.set("Authorization", `Bearer ${auth}`);
  return fetch(`${API}${path}`, { ...init, headers }).then((r) => {
    if (r.status === 401 && auth && typeof window !== "undefined") {
      clearAuthToken(); // the session expired or was revoked
      window.dispatchEvent(new Event("deskmates-signed-out"));
    }
    return r;
  });
}
