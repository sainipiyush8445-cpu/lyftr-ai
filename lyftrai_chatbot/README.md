# Lyftr AI — Full-Stack LLM Chatbot with Dual-Key Failover & Caching

A production-ready full-stack AI chatbot application built with React 19, TypeScript, Vite, Tailwind CSS, FastAPI, LiteLLM, Google Gemini, and SQLite (SQLAlchemy ORM with WAL mode).

Features sequential multi-key failover across Gemini API keys, process-local in-memory response caching, atomic conversation turn persistence, per-response token tracking, failure simulation for evaluation, and responsive design.

---

## 1. Architecture Overview

```
                      ┌────────────────────────────────────────┐
                      │          React 19 + Vite SPA           │
                      │  (LocalStorage Session ID + Prompt)    │
                      └──────────────────┬─────────────────────┘
                                         │ HTTP (JSON)
                                         ▼
                      ┌────────────────────────────────────────┐
                      │          FastAPI Backend (Py3)         │
                      │   - Pydantic v2 Request Validation     │
                      │   - Context Window Message Capping     │
                      └───────┬────────────────────────┬───────┘
                              │                        │
               Cache Hit /    │                        │ DB Session
             In-Memory Store  ▼                        ▼
       ┌───────────────────────────────┐   ┌──────────────────────────┐
       │   LiteLLM (InMemoryCache)     │   │     SQLite (WAL Mode)    │
       │   Process-local LRU Cache     │   │   Atomic Two-Turn Commit │
       └──────────────┬────────────────┘   └──────────────────────────┘
                      │ Cache Miss / Live Inference
                      ▼
       ┌───────────────────────────────┐
       │   Multi-Key Failover Engine   │
       │   Key 1 ──(429/5xx/Auth)──► Key 2
       └──────────────┬────────────────┘
                      │
                      ▼
       ┌───────────────────────────────┐
       │       Google Gemini API       │
       │  (gemini/gemini-3.5-flash)    │
       └───────────────────────────────┘
```

---

## 2. Public API Specification

The backend exposes three core endpoints adhering strictly to the evaluator contract:

### `POST /chat`
Executes chat completion with multi-key failover and caching.

- **Headers**:
  - `Content-Type: application/json`
  - `X-Simulate-Failure: <rule>` *(Optional, active when `ENABLE_FAILURE_SIMULATION=true`)*. Example: `key_1=429`, `key_1=500,key_2=503`, `*=429`, `key_1=timeout`.
- **Request Body**:
  ```json
  {
    "session_id": "s-a1b2c3d4",
    "message": "What is the capital of France?",
    "system_prompt": "You are a concise geography assistant."
  }
  ```
  - `session_id`: String, 1–128 chars, matching regex `^[a-zA-Z0-9_-]+$`.
  - `message`: String, 1–4000 chars (stripped of surrounding whitespace).
  - `system_prompt`: Optional string, max 2000 chars.
- **Success Response (`200 OK`)**:
  ```json
  {
    "session_id": "s-a1b2c3d4",
    "reply": "The capital of France is Paris.",
    "metadata": {
      "resolution_source": "LIVE_INFERENCE",
      "model": "gemini/gemini-3.5-flash",
      "latency_ms": 780,
      "input_tokens": 28,
      "output_tokens": 8,
      "key_id": "key_1",
      "fallback_attempts": 0
    }
  }
  ```
  *When served from cache:*
  ```json
  {
    "metadata": {
      "resolution_source": "LLM_CACHE",
      "model": "gemini/gemini-3.5-flash",
      "latency_ms": 1,
      "input_tokens": 28,
      "output_tokens": 8,
      "key_id": null,
      "fallback_attempts": 0
    }
  }
  ```
- **Error Responses**:
  - `400 Bad Request`: Non-retryable client error (e.g., content safety violation).
  - `422 Unprocessable Entity`: Schema/validation constraint violation.
  - `503 Service Unavailable`: All candidate API keys failed or no keys configured:
    ```json
    {"detail": "all providers unavailable"}
    ```

### `GET /sessions/{session_id}/messages`
Retrieves chronological conversation history for a given session.
- **Path Parameter**: `session_id` (`^[a-zA-Z0-9_-]+$`).
- **Response (`200 OK`)**:
  ```json
  {
    "session_id": "s-a1b2c3d4",
    "messages": [
      {
        "role": "user",
        "content": "What is the capital of France?",
        "metadata": null
      },
      {
        "role": "assistant",
        "content": "The capital of France is Paris.",
        "metadata": {
          "resolution_source": "LIVE_INFERENCE",
          "input_tokens": 28,
          "output_tokens": 8
        }
      }
    ]
  }
  ```
- **`404 Not Found`**: Returned if the session does not exist.

### `GET /health`
System monitoring and container readiness probe.
- **Response (`200 OK`)**:
  ```json
  {
    "status": "ok",
    "keys_configured": 2,
    "cache_enabled": true
  }
  ```
- **Response (`503 Service Unavailable`)**: Returned if no keys are configured (`"No API keys configured"`) or if the database is unreachable (`"Database unreachable"`).

---

## 3. Failover Policy & Metric Definitions

### Policy Rules
1. **Candidate Key Discovery**: Keys are pulled from `GEMINI_API_KEY_1` and `GEMINI_API_KEY_2`. Empty or unconfigured keys are skipped immediately without consuming attempts or raising errors.
2. **Sequential Execution**: Candidate keys are tried in strict sequence (`key_1` then `key_2`). Each candidate key is evaluated at most once per request (no endless retry loops).
3. **Retryable Errors (Trigger Failover)**:
   - HTTP Status codes: `401` (Unauthorized/Invalid Key), `403` (Permission Denied), `408` / `504` (Timeout), `429` (Rate Limited / Quota), `500` (Internal Server Error), `502` (Bad Gateway), `503` (Service Unavailable).
   - Network drop, connection resets, and connection timeouts.
   - *Key takeaway:* A bad or unauthorized `key_1` will **not** block a valid `key_2`.
4. **Non-Retryable Errors (No Failover)**:
   - `400 Bad Request` and provider content safety / policy rejections stop immediately and return HTTP 400.
5. **Key Exhaustion**: If all configured candidate keys encounter retryable failures, the endpoint returns HTTP `503` with body `{"detail": "all providers unavailable"}`.

### Metric Definitions
- **`key_id`**: Identifies which API key successfully answered (`"key_1"`, `"key_2"`, or `null` if served from `LLM_CACHE`).
- **`fallback_attempts`**: Integer counting the number of configured keys that actually attempted and failed prior to success.
  - Succeeded on `key_1`: `fallback_attempts = 0`
  - `key_1` failed (e.g. 429), succeeded on `key_2`: `fallback_attempts = 1`
  - Cache hit: `fallback_attempts = 0`
  - `key_1` was unconfigured, `key_2` answered: `fallback_attempts = 0`

---

## 4. Environment Variables

| Variable | Description | Default | Allowed / Example |
| :--- | :--- | :--- | :--- |
| `GEMINI_API_KEY_1` | Primary Google Gemini API key | `""` | `AIzaSy...` |
| `GEMINI_API_KEY_2` | Secondary Google Gemini key (failover) | `""` | `AIzaSy...` |
| `GEMINI_MODEL` | LiteLLM Gemini model identifier | `gemini/gemini-3.5-flash` | `gemini/gemini-3.5-flash` |
| `DATABASE_URL` | SQLAlchemy connection URL | `sqlite:///./data/app.db` | Local: `sqlite:///./data/app.db`<br>Docker: `sqlite:////data/app.db` |
| `ENABLE_FAILURE_SIMULATION`| Enable `X-Simulate-Failure` header | `false` | `true` or `false` |
| `CORS_ORIGINS` | Allowed origins (wildcard, comma, JSON) | `*` | `*` or `http://localhost:3000,https://my.app` |
| `MAX_HISTORY_MESSAGES` | Max previous messages sent to LLM | `20` | `20` |
| `LOG_LEVEL` | Python logging level | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `VITE_API_URL` | Frontend API backend address *(build-time)* | `http://localhost:8000` | `http://localhost:8000` |

> **Note on `VITE_API_URL`**: Vite environment variables are embedded into static JavaScript bundles at **build time**. If hosting the frontend on Vercel and the backend on Render, specify `VITE_API_URL=https://lyftr-backend.onrender.com` during the frontend build step.

---

## 5. Local Setup & Running

### Prerequisites
- Python 3.10+ (tested on Python 3.10 and 3.12)
- Node.js 18+ (tested on Node v22.16.0)
- npm 9+

### Option A: Local Run (Without Docker)

#### 1. Configure Environment
```bash
# Workspace root
cp .env.example .env
# Edit .env and supply GEMINI_API_KEY_1 and GEMINI_API_KEY_2
```

#### 2. Start Backend
**On Windows (PowerShell):**
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r backend/requirements.txt
$env:PYTHONPATH = "backend"
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

**On Linux / macOS (bash):**
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
export PYTHONPATH=backend
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Verify backend:
```bash
curl http://localhost:8000/health
```

#### 3. Start Frontend
In a new terminal:
```bash
cd frontend
npm ci
npm run dev
```
Open your browser at `http://localhost:5173` (or the port Vite outputs).

---

### Option B: With Docker Compose

*Requires Docker Desktop to be running.*

```bash
# 1. Provide your keys in .env
cp .env.example .env

# 2. Build and launch services
docker compose up --build -d

# 3. Check health and logs
docker compose ps
docker compose logs -f

# 4. Access applications
# Frontend: http://localhost:3000
# Backend:  http://localhost:8000/health

# 5. Teardown
docker compose down        # preserves volume data at /data
docker compose down -v     # wipes persistent SQLite volume
```

---

## 6. Running Tests

### Backend Test Suite (Pytest)
Comprehensive suite covering the failover matrix, failure simulation, cache isolation, atomic transactions, session boundaries, and sanitization:
```powershell
# Windows (PowerShell)
.\.venv\Scripts\pytest backend\tests -v

# Linux / macOS
pytest backend/tests -v
```

### Frontend Test Suite (Vitest)
Unit and integration tests for mount restoration, 404/network resilience, new chat isolation, late reply cancellation, and double-submit prevention:
```bash
cd frontend
npm test
```

### Frontend Production Build Check
```bash
cd frontend
npm run build
```

---

## 7. Deployment Instructions

### Deploying Backend to Render
1. Create a new Web Service on [Render](https://render.com) from your Git repository (or connect blueprint via `render.yaml`).
2. Configure settings:
   - **Root Directory**: `backend`
   - **Runtime**: `Python 3`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
   - **Health Check Path**: `/health`
3. Environment Variables:
   - `GEMINI_API_KEY_1`: `<your_key>`
   - `GEMINI_API_KEY_2`: `<your_second_key>`
   - `GEMINI_MODEL`: `gemini/gemini-3.5-flash`
   - `DATABASE_URL`: `sqlite:////data/app.db` *(or `sqlite:////tmp/app.db` on free tier)*
   - `CORS_ORIGINS`: `https://your-frontend-app.vercel.app`
   - `ENABLE_FAILURE_SIMULATION`: `false`
4. **Persistent Disk Note**:
   - Render's **Free Plan** does not support persistent disks. If running on Free tier, omit the `disk` section and use `sqlite:////tmp/app.db` (ephemeral across service spins).
   - For persistent storage, use Render's **Starter Plan** with a 1GB disk mounted at `/data`.

### Deploying Frontend to Vercel
1. Import your Git repository into [Vercel](https://vercel.com).
2. Set **Root Directory** to `frontend`.
3. Configure build settings:
   - **Framework Preset**: Vite
   - **Build Command**: `npm run build`
   - **Output Directory**: `dist`
4. Add Environment Variable:
   - `VITE_API_URL`: URL of your deployed Render backend (e.g., `https://lyftr-backend.onrender.com`).
5. Deploy.

---

## 8. Architectural Limitations & Production Considerations

1. **Process-Local In-Memory Cache**:
   - LiteLLM's `InMemoryCache` resides within the Python process memory.
   - Cache entries are cleared upon process restarts.
   - If running multiple Uvicorn workers (`--workers > 1`) or multi-instance containers, memory is not shared across processes. For multi-replica production setups, backing LiteLLM with a shared Redis instance is recommended.
2. **SQLite Single-Writer Concurrency**:
   - SQLite with WAL (`PRAGMA journal_mode=WAL`) and `PRAGMA busy_timeout=5000` allows concurrent readers and non-blocking concurrent reads while a write occurs.
   - However, SQLite remains a single-writer engine. Under high concurrent write loads (tens of concurrent message insertions per second), transactions wait up to 5 seconds before reporting busy. For higher-scale writes across multiple container instances, PostgreSQL is recommended.
3. **Session ID Charset**:
   - `session_id` is restricted to alphanumeric characters, dashes, and underscores (`^[a-zA-Z0-9_-]+$`) to prevent path traversal and injection issues.
