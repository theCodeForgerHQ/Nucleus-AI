# Nucleus AI — Frontend

Warp-style terminal UI for the Nucleus AI knowledge base. Built with Next.js 14 and Tailwind.

## Setup

1. **Install dependencies**

   ```bash
   cd frontend && npm install
   ```

2. **Environment**

   Copy `.env.local.example` to `.env.local` and set the LLM engine URL if needed:

   ```bash
   cp .env.local.example .env.local
   ```

   Default: `http://localhost:8200` (matches `llm-engine` in docker-compose). The frontend proxies requests via `/api/query`, so no CORS changes are required on the backend.

## Run

### Option A: Docker (recommended — no Node install needed)

From the **repo root** (not inside `frontend/`):

```bash
docker compose up frontend
```

This builds the frontend image (installs dependencies) and starts the dev server. The app is at **http://localhost:3001** (port 3001 is used so it doesn’t clash with Metabase on 3000). The frontend container talks to the `llm-engine` service, so start the stack together if you need the UI to answer queries:

```bash
docker compose up llm-engine reranker-service nli-service frontend
```

### Option B: Local Node

1. **Install dependencies** (required first time): `cd frontend && npm install`
2. **Development:** `npm run dev` — app at [http://localhost:3000](http://localhost:3000)
3. **Production build:** `npm run build && npm start`

Ensure the LLM engine is running (e.g. `docker compose up llm-engine` on port 8200) when using the app.

## Exposing with ngrok

To share or access the app from the internet while running locally:

1. **Install ngrok** — [ngrok.com/download](https://ngrok.com/download) or `choco install ngrok` (Windows).
2. **Start the frontend** — `npm run dev` (so port 3000 is serving the app).
3. **In another terminal**, run either the generic or static-domain command below.

### Using your static ngrok URL

If you have a reserved/static domain (e.g. `patient-husky-uniquely.ngrok-free.app`):

- **Frontend in Docker** (port 3001):  
  `ngrok http 3001 --domain=patient-husky-uniquely.ngrok-free.app`
- **Frontend local** (port 3000):  
  `ngrok http 3000 --domain=patient-husky-uniquely.ngrok-free.app`

Your app will be available at **https://patient-husky-uniquely.ngrok-free.app** whenever the frontend is running and ngrok is started.

### Random URL (no reserved domain)

```bash
ngrok http 3000
```

Use the HTTPS URL ngrok prints. All traffic to that URL is tunneled to your localhost:3000. The app still calls your **local** LLM engine via the Next.js API route (which uses `NEXT_PUBLIC_LLM_ENGINE_URL` on the server), so the backend must be reachable from your machine. If you need the tunneled URL to hit a public backend, set `NEXT_PUBLIC_LLM_ENGINE_URL` in `.env.local` to your deployed LLM engine URL.
