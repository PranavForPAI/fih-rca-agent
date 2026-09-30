/**
 * Server-only auth utilities.
 * Credentials are read from UI_USERNAME / UI_PASSWORD env vars.
 * Session state is stored in a simple signed cookie ("fhi_session").
 */

const UI_USERNAME = process.env["UI_USERNAME"] ?? "admin";
const UI_PASSWORD = process.env["UI_PASSWORD"] ?? "fhi2024";
const SESSION_COOKIE = "fhi_session";
// Use a fixed secret; for production rotate via SESSION_SECRET env var.
const SECRET = process.env["SESSION_SECRET"] ?? "fhi-static-secret-2024";

/** Constant-time-ish string comparison to avoid timing attacks. */
function safeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

/** Issue a signed session token (username + HMAC-SHA-256 via Web Crypto). */
async function sign(username: string): Promise<string> {
  const enc = new TextEncoder();
  const key = await crypto.subtle.importKey(
    "raw",
    enc.encode(SECRET),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const sig = await crypto.subtle.sign("HMAC", key, enc.encode(username));
  const sigHex = Array.from(new Uint8Array(sig))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
  return `${username}.${sigHex}`;
}

/** Verify a session token previously issued by `sign()`. Returns username or null. */
async function verify(token: string): Promise<string | null> {
  const dot = token.indexOf(".");
  if (dot === -1) return null;
  const username = token.slice(0, dot);
  const expected = await sign(username);
  return safeEqual(token, expected) ? username : null;
}

/** Read the session cookie from a Request and return the authenticated username, or null. */
export async function getAuthUser(request: Request): Promise<string | null> {
  const cookieHeader = request.headers.get("cookie") ?? "";
  const cookies = Object.fromEntries(
    cookieHeader.split(";").map((c) => {
      const [k, ...rest] = c.trim().split("=");
      return [k, rest.join("=")];
    }),
  );
  const token = cookies[SESSION_COOKIE];
  if (!token) return null;
  return verify(decodeURIComponent(token));
}

/** Handle POST /api/login: validate body credentials, set cookie on success. */
export async function handleLogin(request: Request): Promise<Response> {
  let body: { username?: string; password?: string };
  try {
    body = await request.json();
  } catch {
    return Response.json({ message: "Invalid request body." }, { status: 400 });
  }

  const { username = "", password = "" } = body;
  const validUser = safeEqual(username, UI_USERNAME);
  const validPass = safeEqual(password, UI_PASSWORD);

  if (!validUser || !validPass) {
    // Deliberate delay to slow brute-force
    await new Promise((r) => setTimeout(r, 400));
    return Response.json({ message: "Invalid username or password." }, { status: 401 });
  }

  const token = await sign(username);
  const cookie = [
    `${SESSION_COOKIE}=${encodeURIComponent(token)}`,
    "Path=/",
    "HttpOnly",
    "SameSite=Lax",
    // Secure in production:
    // process.env["NODE_ENV"] === "production" ? "Secure" : "",
  ]
    .filter(Boolean)
    .join("; ");

  return Response.json({ ok: true }, { status: 200, headers: { "Set-Cookie": cookie } });
}

/** Handle POST /api/logout: clear the session cookie. */
export function handleLogout(): Response {
  return Response.json(
    { ok: true },
    {
      status: 200,
      headers: {
        "Set-Cookie": `${SESSION_COOKIE}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0`,
      },
    },
  );
}
