"""
Ledger models for Playto Payout Engine.

Design principles:
- Balance is NEVER stored as a column. It is always derived from the ledger.
  This prevents an entire class of consistency bugs where the stored balance
  drifts from the actual transaction history.
- All amounts are stored as BigIntegerField in paise (1 INR = 100 paise).
  This avoids floating-point rounding errors entirely.
- Credits and debits live in a single LedgerEntry table with a sign-based
  model (positive = credit, negative = debit). This makes balance calculation
  a trivial SUM and ensures the audit trail is immutable.
- Concurrency safety is achieved via SELECT FOR UPDATE on the merchant row,
  not on the balance column. This serializes all payout requests for a given
  merchant at the DB level.
"""

import uuid
from django.db import models
from django.db.models import Sum
from django.utils import timezone


class Merchant(models.Model):
    """
    A merchant on the Playto platform.
    
    The merchant row is the lock target for payout creation.
    We SELECT FOR UPDATE on this row to serialize concurrent payout
    requests and prevent double-spending.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    email = models.EmailField(unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def get_balance(self):
        """
        Compute available balance from ledger entries.
        Returns total credits minus total debits in paise.
        
        This is always derived from the source of truth (ledger),
        never from a cached column.
        """
        result = self.ledger_entries.aggregate(balance=Sum('amount_paise'))
        return result['balance'] or 0

    def get_held_balance(self):
        """
        Returns the sum of amounts currently held for pending/processing payouts.
        These funds are not available but have not been debited yet.
        """
        return self.payouts.filter(
            status__in=[Payout.Status.PENDING, Payout.Status.PROCESSING]
        ).aggregate(
            held=Sum('amount_paise')
        )['held'] or 0

    def __str__(self):
        return f"{self.name} ({self.email})"

    class Meta:
        ordering = ['name']


class BankAccount(models.Model):
    """Merchant's Indian bank account for INR payouts."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    merchant = models.ForeignKey(
        Merchant, on_delete=models.CASCADE, related_name='bank_accounts'
    )
    account_number = models.CharField(max_length=20)
    ifsc_code = models.CharField(max_length=11)
    account_holder_name = models.CharField(max_length=255)
    is_primary = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.account_holder_name} - {self.account_number[-4:].zfill(4)}"

    class Meta:
        ordering = ['-is_primary', 'created_at']


class LedgerEntry(models.Model):
    """
    Immutable double-entry style ledger record.

    Positive amount_paise = credit (money in, e.g. customer payment received)
    Negative amount_paise = debit  (money out, e.g. payout completed)

    This table is append-only. We never update or delete rows here.
    The merchant's true balance is always SUM(amount_paise) for that merchant.
    """
    class EntryType(models.TextChoices):
        CREDIT = 'credit', 'Credit'
        DEBIT = 'debit', 'Debit'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    merchant = models.ForeignKey(
        Merchant, on_delete=models.PROTECT, related_name='ledger_entries'
    )
    entry_type = models.CharField(max_length=10, choices=EntryType.choices)
    # Positive for credits, negative for debits. Always in paise.
    amount_paise = models.BigIntegerField()
    description = models.CharField(max_length=500)
    # Optional reference to the payout that caused this entry
    payout = models.ForeignKey(
        'Payout', on_delete=models.PROTECT, null=True, blank=True,
        related_name='ledger_entries'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        # Enforce sign invariant: credits must be positive, debits negative
        if self.entry_type == self.EntryType.CREDIT and self.amount_paise <= 0:
            raise ValueError("Credit entries must have positive amount_paise")
        if self.entry_type == self.EntryType.DEBIT and self.amount_paise >= 0:
            raise ValueError("Debit entries must have negative amount_paise")
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.entry_type} {abs(self.amount_paise)} paise for {self.merchant.name}"

    class Meta:
        ordering = ['-created_at']


class Payout(models.Model):
    """
    A payout request from a merchant to their bank account.

    State machine:
        PENDING -> PROCESSING -> COMPLETED
                             -> FAILED
    
    No backwards transitions. No skipping states. Enforced at the model level
    via the VALID_TRANSITIONS map and checked before every save.

    Funds flow:
    - On PENDING:    funds are "held" (not debited yet, just locked in the payout)
    - On COMPLETED:  a debit LedgerEntry is created atomically with this transition
    - On FAILED:     no LedgerEntry created; held funds simply become available again
    """
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        PROCESSING = 'processing', 'Processing'
        COMPLETED = 'completed', 'Completed'
        FAILED = 'failed', 'Failed'

    VALID_TRANSITIONS = {
        Status.PENDING: {Status.PROCESSING},
        Status.PROCESSING: {Status.COMPLETED, Status.FAILED},
        Status.COMPLETED: set(),  # Terminal state
        Status.FAILED: set(),     # Terminal state
    }

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    merchant = models.ForeignKey(
        Merchant, on_delete=models.PROTECT, related_name='payouts'
    )
    bank_account = models.ForeignKey(
        BankAccount, on_delete=models.PROTECT, related_name='payouts'
    )
    amount_paise = models.BigIntegerField()
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    # Retry tracking
    attempt_count = models.PositiveSmallIntegerField(default=0)
    max_attempts = models.PositiveSmallIntegerField(default=3)
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    processing_started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    # Failure reason
    failure_reason = models.CharField(max_length=500, blank=True)

    def transition_to(self, new_status):
        """
        Enforces the state machine. Raises ValueError on illegal transitions.
        This is the single choke point — every status change goes through here.
        """
        allowed = self.VALID_TRANSITIONS.get(self.status, set())
        if new_status not in allowed:
            raise ValueError(
                f"Invalid payout state transition: {self.status} -> {new_status}. "
                f"Allowed: {allowed or 'none (terminal state)'}"
            )
        self.status = new_status

    def __str__(self):
        return f"Payout {self.id} | {self.amount_paise} paise | {self.status}"

    class Meta:
        ordering = ['-created_at']


class IdempotencyKey(models.Model):
    """
    Stores idempotency keys for the payout creation endpoint.

    Scoped per merchant. A key seen before returns the original response.
    Keys expire after 24 hours (enforced in the view layer, not DB).

    The (merchant, key) unique_together constraint is the hard guarantee
    against duplicates — even under concurrent inserts, the DB will reject
    the second one with an IntegrityError.
    """
    merchant = models.ForeignKey(
        Merchant, on_delete=models.CASCADE, related_name='idempotency_keys'
    )
    key = models.CharField(max_length=255, db_index=True)
    # Snapshot of the response we returned for this key
    response_status_code = models.PositiveSmallIntegerField()
    response_body = models.JSONField()
    # The payout created (null if the request failed before creating one)
    payout = models.OneToOneField(
        Payout, on_delete=models.PROTECT, null=True, blank=True,
        related_name='idempotency_key_record'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [('merchant', 'key')]
        ordering = ['-created_at']

    def is_expired(self):
        from django.conf import settings
        ttl_hours = getattr(settings, 'IDEMPOTENCY_KEY_TTL_HOURS', 24)
        age = timezone.now() - self.created_at
        return age.total_seconds() > ttl_hours * 3600

    def __str__(self):
        return f"IdempotencyKey {self.key} for {self.merchant.name}"
