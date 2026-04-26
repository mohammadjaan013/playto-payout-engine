# EXPLAINER.md

## 1. The Ledger

### Balance calculation query

```python
# In Merchant.get_balance() — payouts/models.py
result = self.ledger_entries.aggregate(balance=Sum('amount_paise'))
return result['balance'] or 0
```

Which Django translates to:
```sql
SELECT COALESCE(SUM(amount_paise), 0)
FROM payouts_ledgerentry
WHERE merchant_id = %s;
```

Available balance (what a merchant can actually withdraw) is computed inside the lock in `_create_payout_atomic`:

```python
total_balance = locked_merchant.ledger_entries.aggregate(
    balance=Sum('amount_paise')
)['balance'] or 0

held_balance = locked_merchant.payouts.filter(
    status__in=[Payout.Status.PENDING, Payout.Status.PROCESSING]
).aggregate(held=Sum('amount_paise'))['held'] or 0

available_balance = total_balance - held_balance
```

### Why credits and debits are modeled this way

I chose a **single-table, sign-based ledger** (positive = credit, negative = debit) over two separate tables or a stored balance column for three reasons:

1. **No balance drift.** A stored `balance` column and a ledger can diverge if any code path credits/debits without updating the column. One table eliminates that class of bug entirely. The balance is always authoritative — it is the sum.

2. **Audit trail is the source of truth.** Every money movement is an immutable append. You can reconstruct any point-in-time balance by filtering on `created_at`. This matters for reconciliation and disputes.

3. **Aggregate is DB-native.** `SUM` on an indexed foreign key column is what PostgreSQL is optimised for. It's one round trip, no Python arithmetic on fetched rows.

The sign invariant (credits must be positive, debits must be negative) is enforced in `LedgerEntry.save()` to make the accounting model impossible to violate accidentally.

---

## 2. The Lock

### Exact code that prevents overdrawing

```python
# payouts/views.py — _create_payout_atomic()

with transaction.atomic():
    # This is the critical primitive.
    # PostgreSQL acquires a row-level exclusive lock on the Merchant row.
    # Any other transaction attempting SELECT FOR UPDATE on the same row
    # will BLOCK here until this transaction commits or rolls back.
    locked_merchant = Merchant.objects.select_for_update().get(id=merchant.id)

    # All balance reads happen AFTER the lock is acquired.
    # We cannot see uncommitted changes from concurrent transactions,
    # and they cannot interfere with our balance check.
    total_balance = locked_merchant.ledger_entries.aggregate(
        balance=Sum('amount_paise')
    )['balance'] or 0

    held_balance = locked_merchant.payouts.filter(
        status__in=[Payout.Status.PENDING, Payout.Status.PROCESSING]
    ).aggregate(held=Sum('amount_paise'))['held'] or 0

    available_balance = total_balance - held_balance

    if amount_paise > available_balance:
        # Reject. Store idempotency record. Return 422.
        ...
        return response_body, response_status

    # Funds available. Create payout.
    payout = Payout.objects.create(...)
    IdempotencyKey.objects.create(...)
# Lock released on transaction commit.
```

**Database primitive: `SELECT FOR UPDATE`**

This is a PostgreSQL row-level exclusive lock. The SQL emitted is:
```sql
SELECT * FROM payouts_merchant WHERE id = %s FOR UPDATE;
```

When Transaction A holds this lock, Transaction B attempting the same `SELECT FOR UPDATE` will block at the database level — not spin-wait in Python, not fail immediately, but queue. When A commits, B acquires the lock, re-reads the balance (which now reflects A's payout), and correctly rejects the second request if funds are exhausted.

**Why this over optimistic locking (version columns)?**

For a payout endpoint, requests are inherently serialised anyway (one merchant withdraws at a time). Pessimistic locking gives predictable behaviour: one waits, one proceeds. Optimistic locking would require retry logic on the client, which under high concurrency causes retry storms. For money-moving code, I prefer the predictability of pessimistic locking.

**Why lock the Merchant row, not a balance column?**

There is no balance column. The balance is derived from the ledger. Locking the Merchant row serialises access to that derivation.

---

## 3. The Idempotency

### How the system knows it has seen a key before

The `IdempotencyKey` model has:
```python
class Meta:
    unique_together = [('merchant', 'key')]
```

On first request:
1. We query `IdempotencyKey.objects.get(merchant=merchant, key=key)` — raises `DoesNotExist`.
2. We proceed with the business logic.
3. Inside `transaction.atomic()` (with the merchant lock held), we call `IdempotencyKey.objects.create(...)` storing the exact response body and status code.

On second request with the same key:
1. `IdempotencyKey.objects.get(...)` succeeds. We check expiry.
2. If not expired, we return `existing.response_body` with `existing.response_status_code` immediately — no lock acquired, no DB writes, no balance check.

### What happens if the first request is in-flight when the second arrives

This is the hard case — both requests pass the initial `get()` check (key not found yet) and race toward `create()`.

Both will enter `transaction.atomic()` and attempt `SELECT FOR UPDATE` on the merchant row. One will acquire the lock; the other blocks. The first commits and creates the `IdempotencyKey` record. When the second acquires the lock, it will attempt `IdempotencyKey.objects.create(...)` with the same `(merchant, key)` — PostgreSQL will raise an `IntegrityError` due to the `UNIQUE` constraint.

We catch this at the view level:

```python
except IntegrityError:
    # We lost the race. Re-fetch the record created by the winner.
    try:
        existing = IdempotencyKey.objects.get(merchant=merchant, key=idempotency_key)
        return Response(existing.response_body, status=existing.response_status_code)
    except IdempotencyKey.DoesNotExist:
        return Response({'error': 'Concurrent request conflict.'}, status=409)
```

The second caller gets the same response as the first. No duplicate payout. The `UNIQUE` constraint is the hard guarantee — even if I had bugs in the application layer, the DB enforces it.

---

## 4. The State Machine

### Where failed-to-completed is blocked

Every status change goes through `Payout.transition_to()`:

```python
# payouts/models.py

VALID_TRANSITIONS = {
    Status.PENDING:     {Status.PROCESSING},
    Status.PROCESSING:  {Status.COMPLETED, Status.FAILED},
    Status.COMPLETED:   set(),  # Terminal — no outgoing transitions
    Status.FAILED:      set(),  # Terminal — no outgoing transitions
}

def transition_to(self, new_status):
    allowed = self.VALID_TRANSITIONS.get(self.status, set())
    if new_status not in allowed:
        raise ValueError(
            f"Invalid payout state transition: {self.status} -> {new_status}. "
            f"Allowed: {allowed or 'none (terminal state)'}"
        )
    self.status = new_status
```

`failed-to-completed` is blocked because `VALID_TRANSITIONS[Status.FAILED]` is an empty set. Any call to `payout.transition_to(Status.COMPLETED)` when `payout.status == 'failed'` raises a `ValueError` before touching the database.

This is a single choke point — there is no other code path to change payout status. The tasks (`_finalize_payout`, `check_stuck_payouts`) all call `transition_to()`, so the check is never bypassed.

---

## 5. The AI Audit

I used Claude and GitHub Copilot throughout. Here is a specific case where the AI wrote subtly wrong code that I caught and corrected.

### The bug: balance check outside the transaction lock

**What AI initially gave me:**

```python
@api_view(['POST'])
def create_payout(request, merchant_id):
    # ... validation ...
    
    merchant = Merchant.objects.get(id=merchant_id)
    balance = merchant.get_balance()           # ← READ OUTSIDE LOCK
    held = merchant.get_held_balance()
    available = balance - held
    
    if amount_paise > available:
        return Response({'error': 'Insufficient balance'}, status=422)
    
    with transaction.atomic():
        payout = Payout.objects.create(...)    # ← WRITE INSIDE TRANSACTION
        IdempotencyKey.objects.create(...)
```

**Why this is wrong:**

This is a classic check-then-act race condition (TOCTOU). The balance is read outside the transaction, but the payout is created inside one. Between the read and the write, a concurrent request can:

1. Request A reads balance: 10000 paise available.
2. Request B reads balance: 10000 paise available (A hasn't committed yet).
3. Request A creates a 6000 paise payout. Commits.
4. Request B creates a 6000 paise payout. Commits. **Overdraw.**

The `SELECT FOR UPDATE` is useless here — there's nothing to protect if the balance check is already done before acquiring the lock.

**What I replaced it with:**

```python
def _create_payout_atomic(merchant, bank_account, amount_paise, idempotency_key):
    with transaction.atomic():
        # Lock FIRST, then read balance
        locked_merchant = Merchant.objects.select_for_update().get(id=merchant.id)
        
        # All reads happen inside the lock boundary
        total_balance = locked_merchant.ledger_entries.aggregate(
            balance=Sum('amount_paise')
        )['balance'] or 0

        held_balance = locked_merchant.payouts.filter(
            status__in=[Payout.Status.PENDING, Payout.Status.PROCESSING]
        ).aggregate(held=Sum('amount_paise'))['held'] or 0
        
        available_balance = total_balance - held_balance

        if amount_paise > available_balance:
            # ... 422 response ...
        
        payout = Payout.objects.create(...)
        IdempotencyKey.objects.create(...)
```

The lock acquisition, balance read, and payout creation are all inside the same `transaction.atomic()` block. PostgreSQL guarantees that no other transaction can read or write the locked merchant row until this transaction commits. The balance we see is the committed state of the world at the moment we hold the lock.

This was the most important correction. The AI generated syntactically valid, logically reasonable-looking code that would have been a production incident waiting to happen under concurrent load.
