/**
 * Asking for an account, and redeeming an approved request.
 *
 * Apart from api/client.ts for the same reason authApi.ts is: these calls run
 * with no session at all, so the 401 handler that means "your session expired"
 * has nothing to say about them.
 *
 * The one rule that matters here is not to be more helpful than the server.
 * /api/access-requests answers identically whether or not the address already
 * has an account, and /claim answers identically for a wrong, expired, spent
 * or denied token — so that a public form cannot be used to discover who
 * trades here, or to probe the token space with feedback. Any attempt to
 * enrich those messages on the client puts the oracle straight back.
 */

const CREDENTIALS: RequestCredentials = "same-origin";

/** The routes 404 while AUTH_ENABLED is false — there are no accounts to grant. */
export class AccountsDisabledError extends Error {
  constructor() {
    super("This instance does not use accounts.");
    this.name = "AccountsDisabledError";
  }
}

export class AccessError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
    this.name = "AccessError";
  }
}

async function post(path: string, body: unknown): Promise<Response> {
  return fetch(path, {
    method: "POST",
    credentials: CREDENTIALS,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

/**
 * A 404 means the route is not mounted; anything else that is not ok is a
 * failure worth reporting. Distinguished because "this instance has no
 * accounts" is a different thing to tell someone than "try again".
 */
function raise(status: number, fallback: string): never {
  if (status === 404) throw new AccountsDisabledError();
  if (status === 429) {
    throw new AccessError(
      "Too many requests from this connection. Try again later.", status);
  }
  if (status === 422) {
    throw new AccessError("Check the details and try again.", status);
  }
  throw new AccessError(fallback, status);
}

export async function requestAccess(email: string, reason: string): Promise<void> {
  let res: Response;
  try {
    res = await post("/api/access-requests", { email, reason: reason || null });
  } catch {
    throw new AccessError("Could not reach the server.", 0);
  }
  if (res.ok) return;
  raise(res.status, "The request could not be sent. Try again shortly.");
}

export async function claimAccess(token: string, password: string): Promise<void> {
  let res: Response;
  try {
    res = await post("/api/access-requests/claim", { token, password });
  } catch {
    throw new AccessError("Could not reach the server.", 0);
  }
  if (res.ok) return;
  if (res.status === 400) {
    // Echoed as one message, deliberately vague, because the server returns
    // one 400 for wrong / expired / already-used / denied. Guessing at which
    // one it was would hand back the distinction the server just removed.
    throw new AccessError(
      "That setup link is not valid. It may have expired or already been used — "
      + "ask the operator for a new one.", res.status);
  }
  raise(res.status, "The account could not be created. Try again shortly.");
}
