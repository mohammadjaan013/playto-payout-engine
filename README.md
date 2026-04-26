# Playto Payout Engine

Cross-border payout infrastructure for Indian merchants. Merchants collect USD from international clients; Playto settles the funds to their Indian bank accounts in INR.

## Architecture

```
┌────────────┐    POST /payouts    ┌─────────────┐    enqueue    ┌───────────────┐
│  React UI  │ ─────────────────→  │  Django DRF │ ────────────→ │ Celery Worker │
└────────────┘                     └─────────────┘               └───────────────┘
                                          │                               │
                                    SELECT FOR UPDATE             Simulate bank
                                    balance check                 70% OK / 20% fail
                                          │                       10% hang → retry
                                     PostgreSQL
                                   (ledger table)
```

**Key design choices:**
- Balance is never stored. It is always `SUM(ledger_entries.amount_paise)` for a merchant.
- All amounts are `BigIntegerField` in paise. No floats. No decimals.
- Concurrency is handled with `SELECT FOR UPDATE` on the merchant row — DB-level, not Python-level.
- Idempotency keys are stored in the DB with a `UNIQUE(merchant, key)` constraint as the hard guarantee.

## Quick Start (Docker)

```bash
git clone <repo>
cd playto-challenge
docker-compose up --build
```

- Frontend: http://localhost:3000
- API: http://localhost:8000/api/v1/
- Admin: http://localhost:8000/admin/

The seed script runs automatically on first boot. Three merchants are pre-loaded with credit history.

## Quick Start (Local)

### Prerequisites
- Python 3.11+
- PostgreSQL 14+
- Redis 7+
- Node.js 18+

### Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Create .env file
cat > .env << EOF
DB_NAME=playto_payout
DB_USER=postgres
DB_PASSWORD=postgres
DB_HOST=localhost
DB_PORT=5432
REDIS_URL=redis://localhost:6379/0
SECRET_KEY=local-dev-secret-key
DEBUG=True
EOF

# Setup DB
createdb playto_payout
python manage.py migrate

# Seed data
python seed.py

# Run Django (in terminal 1)
python manage.py runserver

# Run Celery worker (in terminal 2)
celery -A playto_payout worker --loglevel=info

# Run Celery beat for periodic tasks (in terminal 3)
celery -A playto_payout beat --loglevel=info --scheduler django_celery_beat.schedulers:DatabaseScheduler
```

### Frontend

```bash
cd frontend
npm install
REACT_APP_API_URL=http://localhost:8000/api/v1 npm start
```

### Run Tests

```bash
cd backend
python manage.py test payouts --verbosity=2
```

## API Reference

### Merchants

```
GET  /api/v1/merchants/                          # List all merchants
GET  /api/v1/merchants/{id}/                     # Balance + bank accounts
GET  /api/v1/merchants/{id}/ledger/              # Ledger entries
GET  /api/v1/merchants/{id}/payouts/             # Payout history
```

### Payouts

```
POST /api/v1/merchants/{id}/payouts/create/      # Create payout

Headers:
  Idempotency-Key: <uuid>   (required)

Body:
  {
    "amount_paise": 50000,
    "bank_account_id": "<uuid>"
  }

GET  /api/v1/merchants/{id}/payouts/{payout_id}/ # Payout status
```

### Example: Create Payout

```bash
curl -X POST http://localhost:8000/api/v1/merchants/<MERCHANT_ID>/payouts/create/ \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: $(uuidgen)" \
  -d '{"amount_paise": 50000, "bank_account_id": "<BANK_ACCOUNT_ID>"}'
```

## Payout Lifecycle

```
PENDING → PROCESSING → COMPLETED  (debit entry created atomically)
                     → FAILED     (held funds released, no debit)
```

Payouts stuck in PROCESSING for > 30 seconds are automatically retried with exponential backoff (max 3 attempts), then permanently FAILED.

## Test Coverage

| Test | What it verifies |
|------|-----------------|
| `ConcurrencyTest.test_concurrent_payout_overdraw_one_succeeds` | Two simultaneous 60 INR requests on 100 INR balance → exactly 1 succeeds |
| `IdempotencyTest.test_idempotency_same_key_returns_same_response` | Same key → same response, no duplicate payout |
| `StateTransitionTest.test_failed_to_completed_blocked` | Terminal state cannot transition forward |
| `PayoutLifecycleTest.test_completed_payout_creates_debit_ledger_entry` | Debit entry created atomically with COMPLETED |
| `BalanceIntegrityTest` | Balance always equals ledger sum |
