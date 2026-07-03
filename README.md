# GovSpend

Track expiring U.S. government contracts using the [USAspending API](https://api.usaspending.gov) and a PostgreSQL-backed FastAPI dashboard.

## Features

- Pulls federal **contracts** expiring in the **next 60 days**
- Filters: **$50,000+** awards and target **NAICS** codes
- Stores contracts in **PostgreSQL** with pipeline status tracking
- **Daily auto-refresh** via APScheduler (default 06:00 UTC)
- Dashboard sorted by expiration date with **award amount highlighted**
- Ready to deploy on **Railway**

## NAICS codes tracked

`561720`, `561730`, `115310`, `561990`, `238910`, `562111`, `488490`, `562998`

## Local development

### 1. PostgreSQL

Create a database:

```sql
CREATE DATABASE govspend;
```

### 2. Environment

```bash
cp .env.example .env
```

Edit `.env`:

```
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/govspend
SYNC_ON_STARTUP=true
```

### 3. Install & run

```bash
python -m venv .venv
.venv\Scripts\activate   # Windows
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000

> **Note:** The first sync paginates USAspending results and may take several minutes. Use **Refresh Now** on the dashboard or `POST /api/sync/run`.

## API endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Dashboard UI |
| GET | `/health` | Health check |
| GET | `/api/contracts` | List contracts |
| PATCH | `/api/contracts/{id}/status` | Update pipeline status |
| GET | `/api/stats` | Summary stats |
| POST | `/api/sync/run` | Trigger manual sync |
| GET | `/api/sync/status` | Last sync status |

## Deploy on Railway

1. Create a new Railway project from this repo
2. Add a **PostgreSQL** plugin — Railway sets `DATABASE_URL` automatically
3. Deploy using the included `Procfile`
4. Optional env vars:
   - `REFRESH_HOUR=6` — daily sync hour (UTC)
   - `REFRESH_MINUTE=0`
   - `SYNC_ON_STARTUP=true` — background sync on boot

Railway assigns `PORT`; the Procfile binds to it automatically.

## Contract fields

| Field | Source |
|-------|--------|
| Contract name | USAspending `Description` |
| Award amount | `Award Amount` |
| Agency | `Awarding Agency` |
| Place of performance | `Primary Place of Performance` |
| Incumbent | `Recipient Name` |
| Expiration date | `End Date` |
| Contracting office | Award detail `office_agency_name` |
| CO name | Latest transaction `contracting_officers_name` (when available) |
| Status | Local: Watching / Active / Pursuing / Won / Lost |

## License

MIT
