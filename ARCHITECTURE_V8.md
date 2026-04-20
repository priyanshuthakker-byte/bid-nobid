# Bid/No-Bid v8 Architecture

## Backbone Added

- `core/config.py`: centralized settings.
- `core/database.py`: SQLAlchemy engine/session.
- `core/models.py`: `User`, `TenderRecord`, `WorkItem`.
- `core/auth.py`: password hashing + JWT issue/verify.
- `core/worker.py`: DB-backed worker queue.
- `core/ingestion.py`: source registry scaffold.

## Migration Strategy

Current live APIs remain active for backward compatibility.

New path:

1. Keep existing JSON + Drive paths running.
2. Enable Postgres (`DATABASE_URL`).
3. Sync tenders from JSON into Postgres (`/platform/sync-json-to-postgres`).
4. Shift analytics/reporting to Postgres endpoints.
5. Gradually move write paths from JSON to Postgres.

## Security Model

- JWT bearer auth for v8 platform endpoints.
- Role model: `admin`, `analyst`, `viewer`.
- Existing `ADMIN_TOKEN` routes still supported for legacy flows.

## Worker Model

- `work_items` table is queue source of truth.
- Default handler implemented: `tender_scoring`.
- Extend by registering handlers in startup.

## Next Expansion Blocks

- Add GeM/CPPP/state connectors in `core/ingestion.py`.
- Add clause-evidence table and extracted criteria tables.
- Add organization intelligence and competitor benchmarking tables.
- Add audit trail table and role action logs.

## Phase-2 Delivered

- DB tables added:
  - `tender_sources`
  - `ingested_tenders`
  - `clause_evidence`
- Worker handlers added:
  - `ingestion_sync`
  - `clause_index`
- APIs added for:
  - source management
  - ingestion run + result listing
  - clause evidence indexing and querying
