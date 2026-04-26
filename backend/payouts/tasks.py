"""
Payout processing tasks.

The processor simulates bank settlement with these probabilities:
  70% success  → payout transitions to COMPLETED, debit LedgerEntry created
  20% failure  → payout transitions to FAILED, held funds released
  10% hang     → payout stays in PROCESSING, retry logic picks it up

Retry logic:
  - Payouts stuck in PROCESSING for > 30 seconds are retried by the
    check_stuck_payouts periodic task.
  - Max 3 attempts total. On exhausting retries → FAILED + funds released.
  - Exponential backoff: attempt 1 immediately, attempt 2 after 10s,
    attempt 3 after 30s (handled by the periodic task's countdown).

Atomicity guarantee:
  - The COMPLETED → debit LedgerEntry transition is atomic.
    Either both happen or neither does. This prevents the balance from being
    debited without the payout reaching COMPLETED or vice versa.
  - The FAILED → release funds transition is also atomic: we change the payout
    status inside a transaction. Because there is no LedgerEntry for a held
    payout, releasing it simply means setting status=FAILED. The balance
    calculation will stop including it in held_balance automatically.
"""

import logging
import random
import time

from celery import shared_task
from django.db import transaction
from django.utils import timezone

from .models import Payout, LedgerEntry

logger = logging.getLogger(__name__)

# Simulated bank outcomes
OUTCOME_SUCCESS = 'success'
OUTCOME_FAILURE = 'failure'
OUTCOME_HANG = 'hang'

OUTCOME_WEIGHTS = [
    (OUTCOME_SUCCESS, 0.70),
    (OUTCOME_FAILURE, 0.20),
    (OUTCOME_HANG, 0.10),
]


def _simulate_bank_outcome():
    """
    Returns a simulated outcome from the bank gateway.
    Using random.random() and cumulative thresholds keeps it simple and testable.
    """
    r = random.random()
    cumulative = 0.0
    for outcome, weight in OUTCOME_WEIGHTS:
        cumulative += weight
        if r < cumulative:
            return outcome
    return OUTCOME_SUCCESS  # fallback


@shared_task(bind=True, max_retries=0, name='payouts.process_payout')
def process_payout(self, payout_id: str):
    """
    Main payout processing task.

    Steps:
    1. Load payout and validate it's in a processable state.
    2. Transition to PROCESSING (with optimistic check + DB lock).
    3. Simulate bank gateway call.
    4. Transition to COMPLETED or FAILED atomically.

    We use select_for_update() inside the transition to prevent a
    concurrent retry from processing the same payout simultaneously.
    """
    logger.info("Processing payout %s", payout_id)

    with transaction.atomic():
        try:
            # Lock the payout row to prevent concurrent processing
            payout = Payout.objects.select_for_update().get(id=payout_id)
        except Payout.DoesNotExist:
            logger.error("Payout %s not found", payout_id)
            return

        if payout.status != Payout.Status.PENDING:
            logger.warning(
                "Payout %s is in status %s, expected PENDING. Skipping.",
                payout_id, payout.status
            )
            return

        # Transition to PROCESSING
        try:
            payout.transition_to(Payout.Status.PROCESSING)
        except ValueError as e:
            logger.error("State transition error for payout %s: %s", payout_id, e)
            return

        payout.processing_started_at = timezone.now()
        payout.attempt_count += 1
        payout.save(update_fields=['status', 'processing_started_at', 'attempt_count', 'updated_at'])

    # Transaction committed — payout is now PROCESSING.
    # Simulate the bank call outside the transaction (it's a network call in real life).
    # This means the payout can be in PROCESSING while we're "waiting" on the bank.
    outcome = _simulate_bank_outcome()
    logger.info("Payout %s bank outcome: %s", payout_id, outcome)

    if outcome == OUTCOME_HANG:
        # Leave it in PROCESSING. The check_stuck_payouts periodic task
        # will pick it up after PAYOUT_PROCESSING_TIMEOUT_SECONDS.
        logger.info(
            "Payout %s is hanging in PROCESSING. Will be retried by stuck-payout checker.",
            payout_id
        )
        return

    _finalize_payout(payout_id, outcome)


def _finalize_payout(payout_id: str, outcome: str):
    """
    Finalize a payout as COMPLETED or FAILED.
    This is atomic: the status change and any ledger write happen together.
    """
    with transaction.atomic():
        try:
            payout = Payout.objects.select_for_update().get(id=payout_id)
        except Payout.DoesNotExist:
            logger.error("Payout %s not found during finalization", payout_id)
            return

        if payout.status != Payout.Status.PROCESSING:
            logger.warning(
                "Payout %s in unexpected state %s during finalization. Skipping.",
                payout_id, payout.status
            )
            return

        if outcome == OUTCOME_SUCCESS:
            try:
                payout.transition_to(Payout.Status.COMPLETED)
            except ValueError as e:
                logger.error("Cannot complete payout %s: %s", payout_id, e)
                return

            payout.completed_at = timezone.now()
            payout.save(update_fields=['status', 'completed_at', 'updated_at'])

            # Create the debit ledger entry atomically with the COMPLETED transition.
            # amount_paise is negative for debits.
            LedgerEntry.objects.create(
                merchant=payout.merchant,
                entry_type=LedgerEntry.EntryType.DEBIT,
                amount_paise=-payout.amount_paise,
                description=f"Payout to bank account {payout.bank_account.account_number[-4:]}",
                payout=payout,
            )
            logger.info("Payout %s completed successfully.", payout_id)

        elif outcome == OUTCOME_FAILURE:
            try:
                payout.transition_to(Payout.Status.FAILED)
            except ValueError as e:
                logger.error("Cannot fail payout %s: %s", payout_id, e)
                return

            payout.failure_reason = "Bank gateway declined the transfer."
            payout.save(update_fields=['status', 'failure_reason', 'updated_at'])
            # No LedgerEntry needed. The held funds are automatically released
            # because get_held_balance() only counts PENDING/PROCESSING payouts.
            logger.info("Payout %s failed. Funds released back to merchant balance.", payout_id)


@shared_task(name='payouts.check_stuck_payouts')
def check_stuck_payouts():
    """
    Periodic task: find payouts stuck in PROCESSING and retry or fail them.

    "Stuck" = in PROCESSING state for more than PAYOUT_PROCESSING_TIMEOUT_SECONDS.

    Retry strategy:
      - attempt_count < max_attempts: retry (dispatch process_payout again)
      - attempt_count >= max_attempts: mark as FAILED, release funds

    This task should be scheduled to run every 10 seconds via Celery Beat.
    """
    from django.conf import settings
    timeout_seconds = getattr(settings, 'PAYOUT_PROCESSING_TIMEOUT_SECONDS', 30)

    cutoff_time = timezone.now() - timezone.timedelta(seconds=timeout_seconds)

    stuck_payouts = Payout.objects.filter(
        status=Payout.Status.PROCESSING,
        processing_started_at__lt=cutoff_time,
    ).select_related('merchant', 'bank_account')

    for payout in stuck_payouts:
        logger.warning(
            "Found stuck payout %s (attempt %d/%d, started %s)",
            payout.id, payout.attempt_count, payout.max_attempts,
            payout.processing_started_at
        )

        if payout.attempt_count < payout.max_attempts:
            # Retry: reset to PENDING so process_payout can pick it up
            with transaction.atomic():
                # Re-fetch with lock to avoid race with concurrent checker runs
                try:
                    locked_payout = Payout.objects.select_for_update(nowait=True).get(
                        id=payout.id, status=Payout.Status.PROCESSING
                    )
                except Payout.DoesNotExist:
                    continue
                except Exception:
                    # Another checker instance got it first
                    continue

                # Exponential backoff countdown in seconds: 10s, 30s
                backoff = 10 * (2 ** (locked_payout.attempt_count - 1))

                locked_payout.status = Payout.Status.PENDING
                locked_payout.processing_started_at = None
                locked_payout.save(update_fields=['status', 'processing_started_at', 'updated_at'])

            process_payout.apply_async(args=[str(payout.id)], countdown=backoff)
            logger.info(
                "Retrying payout %s with backoff %ds (attempt %d will be %d)",
                payout.id, backoff, payout.attempt_count, payout.attempt_count + 1
            )
        else:
            # Exhausted retries → FAILED
            with transaction.atomic():
                try:
                    locked_payout = Payout.objects.select_for_update(nowait=True).get(
                        id=payout.id, status=Payout.Status.PROCESSING
                    )
                except Payout.DoesNotExist:
                    continue
                except Exception:
                    continue

                try:
                    locked_payout.transition_to(Payout.Status.FAILED)
                except ValueError as e:
                    logger.error(
                        "Cannot fail stuck payout %s: %s", payout.id, e
                    )
                    continue

                locked_payout.failure_reason = (
                    f"Bank gateway did not respond after {locked_payout.max_attempts} attempts."
                )
                locked_payout.save(update_fields=['status', 'failure_reason', 'updated_at'])

            logger.warning(
                "Payout %s permanently failed after %d attempts. Funds released.",
                payout.id, payout.max_attempts
            )
