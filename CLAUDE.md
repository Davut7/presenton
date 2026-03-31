# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Presenton is an AI-powered presentation generator. It has two deployment modes: **Docker** (production API) and **Electron** (desktop app). Both share similar backend/frontend code but in separate directories.

## Architecture

```
├── servers/fastapi/     # Backend API (Docker deployment)
├── servers/nextjs/      # Frontend (Docker deployment)
├── electron/servers/    # Backend + Frontend (Electron desktop)
├── Dockerfile           # Docker production build
├── start.js             # Orchestrator: spawns FastAPI, Next.js, Nginx, MCP
└── nginx.conf           # Reverse proxy config
```

**Runtime services (inside Docker):**
- **Nginx** (port 80) — reverse proxy, serves static files
- **FastAPI** (port 8000) — LLM orchestration, image gen, PPTX export
- **Next.js** (port 3000) — React frontend, Puppeteer-based export
- **MCP Server** (port 8001) — Model Context Protocol

**Key data flow:** User request → outline generation (LLM streaming) → structure/layout selection (LLM) → slide content generation (LLM, batched) → image fetching (Pexels/Pixabay/AI) → PPTX export (Puppeteer → python-pptx)

## Build & Run

```bash
# Docker (production)
docker build -t presenton:local -f Dockerfile .
docker run -d --name presenton -p 5000:80 -v ./app_data:/app_data \
  -e LLM=google -e GOOGLE_API_KEY=key1,key2 \
  -e IMAGE_PROVIDER=pexels -e PEXELS_API_KEY=xxx \
  presenton:local

# Electron (development)
cd electron && npm run setup:env && npm run dev

# FastAPI standalone
cd servers/fastapi
export APP_DATA_DIRECTORY=/tmp/app_data TEMP_DIRECTORY=/tmp/presenton
python server.py --port 8000 --reload true

# Next.js standalone
cd servers/nextjs && npm install && npm run dev
```

## Testing

```bash
# All tests locally
./test-local.sh

# FastAPI tests
cd servers/fastapi
export APP_DATA_DIRECTORY=/tmp/app_data TEMP_DIRECTORY=/tmp/presenton DISABLE_IMAGE_GENERATION=true
python -m pytest tests/ -v

# Next.js lint + build check
cd servers/nextjs && npm run lint && npm run build
```

## Key Design Decisions

- **Dual codebase**: `servers/` (Docker) and `electron/servers/` (desktop) are separate copies. Changes must be synced manually.
- **Multi-key rotation**: `GOOGLE_API_KEY` supports comma-separated keys for round-robin load balancing (`_GoogleKeyRotator` in `llm_client.py`).
- **API key auth**: External requests require `X-API-Key` header when `API_KEY` env is set. Internal requests (no `X-Forwarded-For`) bypass auth.
- **Nginx dual-server**: Internal server (127.0.0.1:80) allows full access for Puppeteer. External server (0.0.0.0:80) only exposes `/api/v1/` and exports.
- **Image providers**: Stock (Pexels/Pixabay) return HTTP URLs directly. AI generators (DALL-E, Gemini, ComfyUI) save to disk and return `ImageAsset` objects.
- **PPTX export pipeline**: FastAPI → calls Next.js Puppeteer endpoint → renders slides in headless Chrome → extracts element attributes → converts to python-pptx model → downloads network images → generates .pptx file.
- **Slide content generation**: LLM generates JSON matching a Zod-derived JSON Schema. `__image_url__` is removed from schema before LLM call; `__image_prompt__` is kept. After LLM response, images are fetched and `__image_url__` is injected.
- **ChromaDB**: Used for icon search (ONNX embeddings). Model must be pre-downloaded in Dockerfile to avoid runtime timeouts.

## Common Pitfalls

- Puppeteer `networkidle0` fires before React SPA finishes rendering. Always use `waitForSelector('[data-speaker-note]')` after `page.goto()`.
- `get_google_api_key_env()` returns only the first key (for validation). The rotator reads all keys directly from `os.getenv("GOOGLE_API_KEY")`.
- Google's structured output may return truncated JSON or empty responses — retry logic is essential in `generate_slide_content.py`.
- `process_old_and_new_slides_and_fetch_assets` uses separate index counters for fetched vs reused images — don't enumerate `new_images` directly.
- Port 5000 is often taken by AirPlay on macOS — use 5050 for local testing.
