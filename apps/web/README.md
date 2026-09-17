# Norma AI — web

The Next.js control-plane frontend: authentication, organizations and
workspaces, the assistant editor, knowledge management, and the in-browser
test call.

## Local development

The app expects the API and the voice worker to be running, which
`docker compose up` in the repository root provides:

```bash
npm install
npm run dev          # http://localhost:3000
```

| command | does |
| --- | --- |
| `npm run dev` | development server |
| `npm run build` | production build |
| `npm test` | unit tests (vitest) |
| `npm run test:e2e` | end-to-end tests (playwright) |
| `npm run lint` | eslint |

## Deploying to Vercel

This app is the only part of Norma AI that belongs on Vercel. The API is a
long-running FastAPI service and the voice worker holds WebSockets open for
the length of a call — neither survives a platform that may reclaim an
instance mid-request (see CLAUDE.md section 38, and `render.yaml` in the
repository root for the API).

**Deploy the API first.** The frontend talks to it from the browser, so
until it has a public URL there is nothing to log in against.

### Project settings

| setting | value |
| --- | --- |
| Root Directory | `apps/web` |
| Framework Preset | Next.js (auto-detected) |
| Build / Install Command | leave as detected |

`vercel.json` here sets only what the defaults do not cover: an
`ignoreCommand` so a commit that touches only the API or the voice worker
does not trigger a frontend rebuild.

### Environment variables

| name | example | notes |
| --- | --- | --- |
| `NEXT_PUBLIC_API_URL` | `https://norma-api.onrender.com` | no trailing slash |
| `NEXT_PUBLIC_VOICE_WS_URL` | `wss://norma-voice.fly.dev` | `wss://`, not `ws://` — a page served over HTTPS cannot open an insecure WebSocket |
| `NEXT_PUBLIC_ECHO_GATE` | unset | optional test-call diagnostic |

These are `NEXT_PUBLIC_`, so they are baked into the client bundle at build
time: changing one requires a redeploy, not just a restart. They are public
by construction — never put a secret behind that prefix.

### After deploying

Set `CORS_ORIGINS` on the API to this deployment's origin. Until you do,
every request from the browser is refused and the login form fails with a
network error rather than anything explanatory.

Then check, in order:

1. `/login` renders and a login succeeds — proves API reachability and CORS.
2. `/assistants` lists assistants — proves authenticated requests work.
3. `/assistants/<id>/test-call` connects — proves the voice worker's
   WebSocket URL and the session ticket signing key match the API's.

Step 3 fails while `apps/voice` is undeployed. That is expected, and it is
the last piece of the deploy order.
