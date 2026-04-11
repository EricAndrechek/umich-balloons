import { Hono } from "hono";
import type { Env } from "./lib/types";
import { launchRoutes } from "./routes/launches";
import { dashboardRoutes } from "./routes/dashboard";
import { leaderboardRoutes } from "./routes/leaderboard";
import { handleCron } from "./cron/poll";

// Worker is same-origin with the SPA (served via [assets] binding), so no CORS
// middleware is needed. Anything the Worker doesn't match below is delegated
// to the assets binding, which applies the `single-page-application` fallback
// from wrangler.toml — so routes like /launch/1 return index.html.
const app = new Hono<{ Bindings: Env }>();

app.get("/health", (c) => c.json({ ok: true }));

// Public GET endpoints — accessible without auth. Everything else under /api/*
// (including GET /api/launches, which lists all groups for the admin page,
// and all mutation verbs) requires an admin password bearer token.
const PUBLIC_GET_PATTERNS: RegExp[] = [
  /^\/api\/launches\/active\/?$/,
  /^\/api\/launches\/\d+\/dashboard\/?$/,
  /^\/api\/launches\/\d+\/telemetry\/?$/,
  /^\/api\/launches\/\d+\/leaderboard\/?$/,
  /^\/api\/launches\/\d+\/competition\/?$/,
];

app.use("/api/*", async (c, next) => {
  const path = new URL(c.req.url).pathname;
  const isPublic =
    c.req.method === "GET" && PUBLIC_GET_PATTERNS.some((re) => re.test(path));
  if (isPublic) return next();

  const auth = c.req.header("Authorization") ?? "";
  const match = auth.match(/^Bearer\s+(.+)$/i);
  const expected = c.env.ADMIN_PASSWORD;
  if (!expected || !match || match[1] !== expected) {
    return c.json({ error: "unauthorized" }, 401);
  }
  return next();
});

app.route("/api/launches", launchRoutes);
app.route("/api/launches", dashboardRoutes);
app.route("/api/launches", leaderboardRoutes);

// Fall through to static assets for everything else (SPA fallback included).
app.all("*", (c) => c.env.ASSETS.fetch(c.req.raw));

// Cloudflare cron expressions can't fire faster than once per minute, so we
// fan out each scheduled invocation into N sub-polls separated by SUB_POLL_MS.
// 3 polls / 20s gap gives effective ~20s SondeHub poll cadence — well within
// Workers Paid wall-time budgets. handleCron is dedup-safe across overlapping
// runs (it pre-loads a 20-minute window of existing contact keys), so back-to-
// back invocations don't double-write aggregates.
const SUB_POLLS_PER_TICK = 3;
const SUB_POLL_MS = 20_000;

async function fanOutCron(env: Env): Promise<void> {
  for (let i = 0; i < SUB_POLLS_PER_TICK; i++) {
    try {
      await handleCron(env);
    } catch (err) {
      console.error("sub-poll failed:", err);
    }
    if (i < SUB_POLLS_PER_TICK - 1) {
      await new Promise((r) => setTimeout(r, SUB_POLL_MS));
    }
  }
}

export default {
  fetch: app.fetch,

  async scheduled(
    _event: ScheduledEvent,
    env: Env,
    ctx: ExecutionContext,
  ): Promise<void> {
    ctx.waitUntil(fanOutCron(env));
  },
};
