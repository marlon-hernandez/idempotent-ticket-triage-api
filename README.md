# Idempotent Ticket Triage API

REST service built with FastAPI, SQLAlchemy 2.0, and PostgreSQL for ingesting support tickets with database-level idempotency and asynchronous ticket classification via a local Ollama model.

## Architecture

* **Idempotency at the database layer:** `POST /tickets` requires an `Idempotency-Key` header backed by a `UNIQUE` constraint (`uq_tickets_idempotency_key`) in PostgreSQL. Handling duplicate detection through `IntegrityError` avoids check-then-insert race conditions under concurrent client retries and returns `409 Conflict`.
* **Deferred background processing:** Incoming tickets are stored immediately with `status="PENDING"` and return `202 Accepted`. Classification runs after the HTTP response via FastAPI `BackgroundTasks` using a separate `SessionLocal()` session.
* **Local LLM classification:** The worker queries a local Ollama endpoint (`/api/generate`) to assign a category (`BILLING`, `TECHNICAL_ISSUE`, `ACCOUNT_ACCESS`, `FEATURE_REQUEST`, `GENERAL`), a priority level (`LOW`, `MEDIUM`, `HIGH`, `CRITICAL`), and a one-line summary.

```mermaid
sequenceDiagram
    participant Client
    participant API as FastAPI
    participant DB as PostgreSQL
    participant Worker as Background Worker
    participant Ollama as Ollama (Local)

    Client->>API: POST /tickets (Idempotency-Key)
    API->>DB: INSERT ticket (PENDING)
    alt Duplicate key
        DB-->>API: IntegrityError
        API-->>Client: 409 Conflict
    else Unique key
        DB-->>API: Commit OK
        API->>Worker: Schedule process_ticket_task(ticket_id)
        API-->>Client: 202 Accepted
        Worker->>DB: UPDATE status = PROCESSING
        Worker->>Ollama: POST /api/generate
        Ollama-->>Worker: CATEGORY | PRIORITY | Summary
        Worker->>DB: UPDATE status = COMPLETED, classification
    end
```

## Project Structure

```text
idempotent-ticket-triage-api/
├── tests/
│   └── test_tickets.py     # API, idempotency, and worker tests
├── database.py             # SQLAlchemy engine, pool config, and get_db dependency
├── models.py               # Ticket ORM model, status enum, and unique constraint
├── schemas.py              # Pydantic v2 request and response models
├── tasks.py                # Background worker and Ollama HTTP client
├── main.py                 # FastAPI app and route handlers
├── docker-compose.yml      # Local PostgreSQL 16 container setup
├── .env                    # Default local configuration (ready out of the box)
└── requirements.txt        # Python dependencies
```

## Local Setup

### Prerequisites
* Python 3.10+
* PostgreSQL 14+ (via Docker Compose or a local installation) — or use the built-in SQLite option in `.env`
* [Ollama](https://ollama.com/) running locally

### 1. Clone and install dependencies

**Windows (PowerShell):**
```powershell
git clone https://github.com/marlon-hernandez/idempotent-ticket-triage-api.git
cd idempotent-ticket-triage-api
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

**Linux / macOS:**
```bash
git clone https://github.com/marlon-hernandez/idempotent-ticket-triage-api.git
cd idempotent-ticket-triage-api
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Start the database

The repository includes a pre-configured `.env` file pointing to `localhost:5432/tickets_db`, so no manual `.env` creation is required. Choose whichever database setup fits your machine:

* **Docker Compose (PostgreSQL 16):**
  ```bash
  docker compose up -d
  ```
* **Existing local PostgreSQL:**
  Create the database (`CREATE DATABASE tickets_db;`) and adjust the username/password in `.env` if different from `postgres:postgres`.
* **Run without PostgreSQL (SQLite):**
  Open `.env`, comment out the PostgreSQL `DATABASE_URL`, and uncomment:
  ```ini
  DATABASE_URL=sqlite:///./tickets.db
  ```

### 3. Pull the Ollama model

Make sure Ollama is running locally and pull `llama3.2`:
```bash
ollama pull llama3.2
```

### 4. Start the API server

```bash
uvicorn main:app --reload
```

Tables and constraints are initialized automatically on startup.
* Swagger UI: `http://127.0.0.1:8000/docs`
* ReDoc: `http://127.0.0.1:8000/redoc`

## Configuration (`.env`)

| Variable | Default | Purpose |
| :--- | :--- | :--- |
| `DATABASE_URL` | `postgresql+psycopg2://postgres:postgres@localhost:5432/tickets_db` | Database connection string (`postgresql+psycopg2://...` or `sqlite:///...`). |
| `OLLAMA_API_URL` | `http://localhost:11434/api/generate` | Ollama generation endpoint. |
| `OLLAMA_MODEL` | `llama3.2` | Model name passed to Ollama. |
| `OLLAMA_TIMEOUT` | `30` | Request timeout in seconds for LLM inference. |

## Usage Examples

### Submit a ticket (`POST /tickets`)

**PowerShell:**
```powershell
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/tickets" `
  -Headers @{ "Idempotency-Key" = "req-001" } `
  -ContentType "application/json" `
  -Body '{"text": "Checkout fails with a 500 error when paying by credit card."}'
```

**curl:**
```bash
curl -X POST "http://127.0.0.1:8000/tickets" \
     -H "Content-Type: application/json" \
     -H "Idempotency-Key: req-001" \
     -d '{"text": "Checkout fails with a 500 error when paying by credit card."}'
```

Response (`202 Accepted`):
```json
{
  "id": 1,
  "idempotency_key": "req-001",
  "text": "Checkout fails with a 500 error when paying by credit card.",
  "status": "PENDING",
  "classification": null,
  "created_at": "2026-11-07T16:00:00Z",
  "processed_at": null
}
```

Sending the same `Idempotency-Key` a second time returns `409 Conflict`:
```json
{
  "detail": "Duplicate Idempotency-Key 'req-001'."
}
```

### Check classification status (`GET /tickets/{ticket_id}` & `GET /tickets`)

**PowerShell:**
```powershell
Invoke-RestMethod -Method Get -Uri "http://127.0.0.1:8000/tickets/1"
# Filter tickets by status (PENDING, PROCESSING, COMPLETED, FAILED):
Invoke-RestMethod -Method Get -Uri "http://127.0.0.1:8000/tickets?status=COMPLETED"
```

**curl:**
```bash
curl "http://127.0.0.1:8000/tickets/1"
curl "http://127.0.0.1:8000/tickets?status=COMPLETED"
```

Response (`200 OK`):
```json
{
  "id": 1,
  "idempotency_key": "req-001",
  "text": "Checkout fails with a 500 error when paying by credit card.",
  "status": "COMPLETED",
  "classification": "BILLING | CRITICAL | Credit card checkout fails with a 500 server error.",
  "created_at": "2026-11-07T16:00:00Z",
  "processed_at": "2026-11-07T16:00:02Z"
}
```

## Running Tests

The test suite runs against an isolated in-memory SQLite database and mocks the Ollama HTTP call:

```bash
pytest -v
```

## Troubleshooting

* **`status: "FAILED"` with connection refused on port `11434`:** Start Ollama (`ollama serve` or launch the desktop app) and verify `ollama list` includes `llama3.2`.
* **`OperationalError` connecting to port `5432`:** PostgreSQL is not running or credentials in `.env` differ from your local setup. Start Docker (`docker compose up -d`) or switch `DATABASE_URL` in `.env` to `sqlite:///./tickets.db`.
* **`422 Unprocessable Entity`:** Verify that the `Idempotency-Key` header is included and the JSON body contains a non-empty `text` field.
