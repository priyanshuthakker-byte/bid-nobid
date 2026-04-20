# Bid/No-Bid System

FastAPI-based tender analysis and document generation platform.

## What Was Repaired

- Fixed startup crash when Drive credentials are not configured.
- Fixed background analysis crash caused by reading deleted `all_text`.
- Fixed potential deadlock in milestone/post-award routes (`RLock`).
- Added missing compatibility API routes used by `index.html`.
- Added graceful fallback for PDF merge when LibreOffice/unoconv is unavailable.
- Added runtime/deploy docs and env template.
- Added v8 production backbone: Postgres, JWT auth, worker queue, ingestion registry scaffold.

## Quick Start (Local)

1. Install Python 3.11+.
2. Install dependencies:

```bash
python -m pip install -r requirements.txt
```

3. Set environment variables (minimum):
   - `GEMINI_API_KEY`

4. Run API:

```bash
python -m uvicorn main:app --host 0.0.0.0 --port 10000
```

5. Open:
   - [http://localhost:10000](http://localhost:10000)
   - Health: [http://localhost:10000/healthz](http://localhost:10000/healthz)

## Render Deployment

This repo already contains `render.yaml` with:

- Build: `pip install -r requirements.txt`
- Start: `uvicorn main:app --host 0.0.0.0 --port 10000`
- Health check: `/healthz`

Set these in Render Dashboard:

- Required:
  - `GEMINI_API_KEY`
  - `DATABASE_URL` (Render Postgres connection string)
  - `JWT_SECRET`
- Recommended:
  - `ADMIN_TOKEN`
  - `ALLOWED_ORIGIN`
- Optional:
  - `GDRIVE_CREDENTIALS`
  - `GDRIVE_FOLDER_ID`
  - `GROQ_API_KEY`

## Notes on Optional Features

- **Google Drive sync**: optional. If not configured, app still runs normally.
- **PDF merge**: if LibreOffice/unoconv is not present, API returns `status=unavailable` with a clear message instead of crashing.
- **Tender247 auto-download**: currently returns `unavailable` by design on constrained hosting.

## Key API Endpoints

- `GET /healthz`
- `GET /health/deep`
- `POST /auth/bootstrap-admin`
- `POST /auth/login`
- `GET /auth/me`
- `POST /work-items`
- `GET /work-items/{id}`
- `POST /platform/sync-json-to-postgres`
- `GET /platform/tenders`
- `GET /platform/sources`
- `POST /platform/sources`
- `POST /platform/ingestion/run`
- `POST /platform/ingestion/preview`
- `GET /platform/ingested`
- `POST /platform/clauses/index`
- `GET /platform/clauses`
- `POST /process-files` (analysis job)
- `GET /analyse-status/{job_id}`
- `POST /generate-docs/{t247_id}`
- `POST /tender/{t247_id}/technical-proposal` (UI alias)
- `POST /tender/{t247_id}/merge-pdf` (UI alias)

## Troubleshooting

- `No API key` / AI failures: verify `GEMINI_API_KEY` in Render env.
- `PDF merge unavailable`: install LibreOffice in host image, or use generated DOCX package.
- Data not persistent after redeploy: configure Drive sync (`GDRIVE_CREDENTIALS`, `GDRIVE_FOLDER_ID`).

## First-Time Postgres Setup

1. Attach Render Postgres and set `DATABASE_URL`.
2. Deploy.
3. Bootstrap admin user:
   - `POST /auth/bootstrap-admin` with `{ "username": "...", "password": "..." }`
4. Login:
   - `POST /auth/login` to get bearer token.
5. Migrate existing JSON tenders:
   - `POST /platform/sync-json-to-postgres` with bearer token.

## Ingestion + Clause Intelligence (Phase-2)

1. Create/Update source:
   - `POST /platform/sources`
   - Example body:
     - `{ "name": "cpptest", "source_type": "json_api", "base_url": "https://example.com/tenders.json", "is_active": true }`
2. Run ingestion job:
   - `POST /platform/ingestion/run` with `{ "source_name": "cpptest" }`
2a. Optional dry-run preview:
   - `POST /platform/ingestion/preview` with:
     - `{ "source_type": "cppp_feed", "endpoint": "..." }`
     - `{ "source_type": "state_portal_table", "endpoint": "..." }`
3. Check work item:
   - `GET /work-items/{id}`
4. View ingested records:
   - `GET /platform/ingested`
5. Build clause evidence index:
   - `POST /platform/clauses/index` with `{ "source_record_id": 1 }` or `{ "t247_id": "..." }`
6. Query evidence:
   - `GET /platform/clauses?clause_type=emd`

### Supported source_type values

- `json_api`: JSON array/items endpoint
- `cppp_feed`: CPPP-style RSS/Atom/XML (falls back to HTML parser)
- `state_portal_table`: generic HTML table listing parser