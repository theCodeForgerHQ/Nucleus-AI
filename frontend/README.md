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

- **Development:** `npm run dev` — app at [http://localhost:3000](http://localhost:3000)
- **Production build:** `npm run build && npm start`

Ensure the LLM engine is running (e.g. `docker-compose up llm-engine` on port 8200) when using the app.
