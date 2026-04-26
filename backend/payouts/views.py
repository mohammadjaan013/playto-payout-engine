"""
Views for the Playto Payout Engine API.

Critical design decisions documented inline:

1. Payout creation uses SELECT FOR UPDATE on the Merchant row.
   This serializes ALL payout creation requests for a given merchant
   at the database level. Two concurrent requests will queue at the DB,
   not race at the Python level. This is the correct primitive.

2. Idempotency is checked BEFORE the lock acquisition for the fast path
   (already seen key → return immediately). The lock is only held during
   the actual balance check + payout creation, minimizing contention.

3. Balance check and payout creation happen in the same atomic transaction
   as the lock. The lock is released only when the transaction commits.
   This is why ATOMIC_REQUESTS=False in settings — we need explicit control.
"""

import logging
from django.db import transaction, IntegrityError
from django.db.models import Sum
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import Merchant, BankAccount, LedgerEntry, Payout, IdempotencyKey
from .serializers import (
    MerchantDashboardSerializer,
    CreatePayoutSerializer,
    PayoutSerializer,
    LedgerEntrySerializer,
    BankAccountSerializer,
)
from .tasks import process_payout

logger = logging.getLogger(__name__)


def get_merchant_or_404(merchant_id):
    try:
        return Merchant.objects.get(id=merchant_id)
    except (Merchant.DoesNotExist, Exception):
        return None


@api_view(['GET'])
def merchant_dashboard(request, merchant_id):
    """
    Returns balance, held balance, and bank accounts for a merchant.
    Balance is computed as SUM(amount_paise) over all ledger entries,
    which is always consistent with the actual transaction history.
    """
    merchant = get_merchant_or_404(merchant_id)
    if not merchant:
        return Response({'error': 'Merchant not found.'}, status=status.HTTP_404_NOT_FOUND)

    serializer = MerchantDashboardSerializer(merchant)
    return Response(serializer.data)


@api_view(['GET'])
def merchant_ledger(request, merchant_id):
    """Returns paginated ledger entries for a merchant."""
    merchant = get_merchant_or_404(merchant_id)
    if not merchant:
        return Response({'error': 'Merchant not found.'}, status=status.HTTP_404_NOT_FOUND)

    entries = merchant.ledger_entries.select_related('payout').order_by('-created_at')[:50]
    serializer = LedgerEntrySerializer(entries, many=True)
    return Response(serializer.data)


@api_view(['GET'])
def merchant_payouts(request, merchant_id):
    """Returns payout history for a merchant."""
    merchant = get_merchant_or_404(merchant_id)
    if not merchant:
        return Response({'error': 'Merchant not found.'}, status=status.HTTP_404_NOT_FOUND)

    payouts = merchant.payouts.select_related('bank_account').order_by('-created_at')[:50]
    serializer = PayoutSerializer(payouts, many=True)
    return Response(serializer.data)


@api_view(['POST'])
def create_payout(request, merchant_id):
    """
    Creates a payout request with full idempotency and concurrency safety.

    Headers:
        Idempotency-Key: <uuid>   (required)
        Content-Type: application/json

    Body:
        {
            "amount_paise": 50000,
            "bank_account_id": "<uuid>"
        }

    Idempotency flow:
    1. Check if we've seen this (merchant, key) before.
       - If yes AND not expired → return the stored response immediately.
       - If yes AND expired → treat as new request (key recycled after 24h).
    2. Acquire SELECT FOR UPDATE on the merchant row.
    3. Validate balance, create payout, record idempotency key atomically.

    Concurrency flow:
    - Two simultaneous requests with different keys for the same merchant:
      one will wait at the SELECT FOR UPDATE, re-check balance after the
      first commits, and fail if funds are exhausted.
    - Two simultaneous requests with the SAME key:
      one will win the unique_together insert; the other gets an
      IntegrityError which we catch and re-fetch to return the same response.
    """
    merchant = get_merchant_or_404(merchant_id)
    if not merchant:
        return Response({'error': 'Merchant not found.'}, status=status.HTTP_404_NOT_FOUND)

    idempotency_key = request.headers.get('Idempotency-Key', '').strip()
    if not idempotency_key:
        return Response(
            {'error': 'Idempotency-Key header is required.'},
            status=status.HTTP_400_BAD_REQUEST
        )

    # Fast path: check for existing non-expired idempotency key BEFORE acquiring lock
    try:
        existing = IdempotencyKey.objects.get(merchant=merchant, key=idempotency_key)
        if not existing.is_expired():
            logger.info(
                "Idempotency hit for merchant=%s key=%s", merchant_id, idempotency_key
            )
            return Response(
                existing.response_body,
                status=existing.response_status_code
            )
        # Expired: delete and treat as new
        existing.delete()
    except IdempotencyKey.DoesNotExist:
        pass

    # Validate request body
    serializer = CreatePayoutSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    amount_paise = serializer.validated_data['amount_paise']
    bank_account_id = serializer.validated_data['bank_account_id']

    # Validate bank account belongs to this merchant
    try:
        bank_account = BankAccount.objects.get(id=bank_account_id, merchant=merchant)
    except BankAccount.DoesNotExist:
        return Response(
            {'error': 'Bank account not found or does not belong to this merchant.'},
            status=status.HTTP_404_NOT_FOUND
        )

    try:
        response_body, response_status = _create_payout_atomic(
            merchant, bank_account, amount_paise, idempotency_key
        )
        return Response(response_body, status=response_status)

    except IntegrityError:
        # Race condition: two concurrent requests with the same idempotency key
        # both passed the fast-path check and both tried to insert.
        # One committed; we lost. Re-fetch and return the stored response.
        try:
            existing = IdempotencyKey.objects.get(merchant=merchant, key=idempotency_key)
            return Response(existing.response_body, status=existing.response_status_code)
        except IdempotencyKey.DoesNotExist:
            logger.error(
                "IntegrityError but no idempotency record found. merchant=%s key=%s",
                merchant_id, idempotency_key
            )
            return Response(
                {'error': 'Concurrent request conflict. Please retry.'},
                status=status.HTTP_409_CONFLICT
            )


def _create_payout_atomic(merchant, bank_account, amount_paise, idempotency_key):
    """
    The critical section. Everything here runs inside a single DB transaction
    with the merchant row locked via SELECT FOR UPDATE.

    Lock scope:
    - We lock the Merchant row, not a balance column (there is none).
    - This forces all payout creation for a merchant to be sequential at the DB.
    - The balance is computed inside the lock, so we see the committed state
      of all previous payouts.

    Why SELECT FOR UPDATE and not optimistic locking?
    - Optimistic locking (check version, retry on conflict) is fine for low
      contention. For a payout engine where every request touches the same
      merchant row, pessimistic locking has predictable latency and no retry
      storms. The lock is held for milliseconds (one aggregation + one insert).
    """
    with transaction.atomic():
        # Lock the merchant row. Any other transaction that tries to lock the
        # same row will block here until we commit or roll back.
        locked_merchant = Merchant.objects.select_for_update().get(id=merchant.id)

        # Compute available balance inside the lock.
        # We must recompute here — the balance from before the lock is stale.
        # Available = total ledger sum - currently held by pending/processing payouts
        total_balance = locked_merchant.ledger_entries.aggregate(
            balance=Sum('amount_paise')
        )['balance'] or 0

        held_balance = locked_merchant.payouts.filter(
            status__in=[Payout.Status.PENDING, Payout.Status.PROCESSING]
        ).aggregate(held=Sum('amount_paise'))['held'] or 0

        available_balance = total_balance - held_balance

        if amount_paise > available_balance:
            response_body = {
                'error': 'Insufficient balance.',
                'available_paise': available_balance,
                'requested_paise': amount_paise,
            }
            response_status = status.HTTP_422_UNPROCESSABLE_ENTITY

            # Store the failed response under the idempotency key too,
            # so a retry with the same key gets the same 422.
            IdempotencyKey.objects.create(
                merchant=locked_merchant,
                key=idempotency_key,
                response_status_code=response_status,
                response_body=response_body,
                payout=None,
            )
            return response_body, response_status

        # Funds are available. Create the payout in PENDING state.
        # No LedgerEntry yet — funds are "held" until the payout resolves.
        payout = Payout.objects.create(
            merchant=locked_merchant,
            bank_account=bank_account,
            amount_paise=amount_paise,
            status=Payout.Status.PENDING,
        )

        payout_data = PayoutSerializer(payout).data
        response_body = {
            'payout': payout_data,
            'message': 'Payout created successfully. Processing will begin shortly.',
        }
        # JSON serialization: convert UUIDs to strings
        response_body['payout'] = _serialize_for_json(payout_data)
        response_status = status.HTTP_201_CREATED

        # Record the idempotency key atomically with the payout creation.
        # If this insert fails (duplicate key race), the whole transaction rolls back
        # and the caller catches the IntegrityError.
        IdempotencyKey.objects.create(
            merchant=locked_merchant,
            key=idempotency_key,
            response_status_code=response_status,
            response_body=response_body,
            payout=payout,
        )

    # Transaction committed. Now dispatch the background worker.
    # This is outside the transaction — we don't want the Celery task
    # to be dispatched if the transaction rolls back.
    process_payout.apply_async(args=[str(payout.id)], countdown=1)

    return response_body, response_status


def _serialize_for_json(data):
    """Convert UUIDs and other non-JSON-native types to strings."""
    import uuid
    from datetime import datetime
    if isinstance(data, dict):
        return {k: _serialize_for_json(v) for k, v in data.items()}
    if isinstance(data, list):
        return [_serialize_for_json(v) for v in data]
    if isinstance(data, uuid.UUID):
        return str(data)
    if isinstance(data, datetime):
        return data.isoformat()
    return data


@api_view(['GET'])
def payout_detail(request, merchant_id, payout_id):
    """Returns current status of a specific payout."""
    merchant = get_merchant_or_404(merchant_id)
    if not merchant:
        return Response({'error': 'Merchant not found.'}, status=status.HTTP_404_NOT_FOUND)

    try:
        payout = Payout.objects.get(id=payout_id, merchant=merchant)
    except Payout.DoesNotExist:
        return Response({'error': 'Payout not found.'}, status=status.HTTP_404_NOT_FOUND)

    serializer = PayoutSerializer(payout)
    return Response(serializer.data)


@api_view(['GET'])
def list_merchants(request):
    """List all merchants (for the frontend merchant selector)."""
    merchants = Merchant.objects.prefetch_related('bank_accounts').all()
    serializer = MerchantDashboardSerializer(merchants, many=True)
    return Response(serializer.data)
