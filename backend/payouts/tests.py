"""
Tests for the Playto Payout Engine.

Two required tests:
1. test_concurrent_payout_overdraw  - concurrency: only one of two simultaneous
   60 INR payout requests on a 100 INR balance should succeed.
2. test_idempotency_same_key_returns_same_response - idempotency: calling the
   payout endpoint twice with the same Idempotency-Key returns the identical
   response without creating a duplicate payout.

Additional tests for state machine and balance integrity.
"""

import threading
import uuid
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework import status

from payouts.models import Merchant, BankAccount, LedgerEntry, Payout, IdempotencyKey
from payouts.tasks import _finalize_payout, OUTCOME_SUCCESS, OUTCOME_FAILURE


def _setup_merchant(name, email, balance_paise):
    """Helper: create a merchant with a given opening balance."""
    merchant = Merchant.objects.create(name=name, email=email)
    bank = BankAccount.objects.create(
        merchant=merchant,
        account_number="00000000000001",
        ifsc_code="HDFC0000001",
        account_holder_name=name,
        is_primary=True,
    )
    LedgerEntry.objects.create(
        merchant=merchant,
        entry_type=LedgerEntry.EntryType.CREDIT,
        amount_paise=balance_paise,
        description="Opening balance for test",
    )
    return merchant, bank


class BalanceIntegrityTest(TestCase):
    """Balance is always derived from the ledger, never stored."""

    def test_balance_equals_sum_of_ledger_entries(self):
        merchant, _ = _setup_merchant("Test Merchant", "test@test.com", 100_000)
        self.assertEqual(merchant.get_balance(), 100_000)

    def test_balance_after_credit_and_debit(self):
        merchant, _ = _setup_merchant("Test Merchant 2", "test2@test.com", 500_000)
        LedgerEntry.objects.create(
            merchant=merchant,
            entry_type=LedgerEntry.EntryType.DEBIT,
            amount_paise=-200_000,
            description="Test debit",
        )
        self.assertEqual(merchant.get_balance(), 300_000)

    def test_no_float_amounts(self):
        """Ensure BigIntegerField stores amounts without precision loss."""
        merchant, _ = _setup_merchant("Test Merchant 3", "test3@test.com", 1)
        entry = merchant.ledger_entries.first()
        self.assertIsInstance(entry.amount_paise, int)

    def test_credit_sign_invariant(self):
        merchant, _ = _setup_merchant("Test Merchant 4", "test4@test.com", 1000)
        with self.assertRaises(ValueError):
            LedgerEntry.objects.create(
                merchant=merchant,
                entry_type=LedgerEntry.EntryType.CREDIT,
                amount_paise=-500,  # Wrong sign for credit
                description="Bad credit",
            )

    def test_debit_sign_invariant(self):
        merchant, _ = _setup_merchant("Test Merchant 5", "test5@test.com", 1000)
        with self.assertRaises(ValueError):
            LedgerEntry.objects.create(
                merchant=merchant,
                entry_type=LedgerEntry.EntryType.DEBIT,
                amount_paise=500,  # Wrong sign for debit
                description="Bad debit",
            )


class StateTransitionTest(TestCase):
    """State machine enforces legal transitions only."""

    def setUp(self):
        self.merchant, self.bank = _setup_merchant("SM Merchant", "sm@test.com", 500_000)

    def _make_payout(self):
        return Payout.objects.create(
            merchant=self.merchant,
            bank_account=self.bank,
            amount_paise=10_000,
            status=Payout.Status.PENDING,
        )

    def test_pending_to_processing_allowed(self):
        p = self._make_payout()
        p.transition_to(Payout.Status.PROCESSING)
        self.assertEqual(p.status, Payout.Status.PROCESSING)

    def test_processing_to_completed_allowed(self):
        p = self._make_payout()
        p.transition_to(Payout.Status.PROCESSING)
        p.transition_to(Payout.Status.COMPLETED)
        self.assertEqual(p.status, Payout.Status.COMPLETED)

    def test_processing_to_failed_allowed(self):
        p = self._make_payout()
        p.transition_to(Payout.Status.PROCESSING)
        p.transition_to(Payout.Status.FAILED)
        self.assertEqual(p.status, Payout.Status.FAILED)

    def test_completed_to_pending_blocked(self):
        """Terminal state: completed cannot go backwards."""
        p = self._make_payout()
        p.transition_to(Payout.Status.PROCESSING)
        p.transition_to(Payout.Status.COMPLETED)
        with self.assertRaises(ValueError):
            p.transition_to(Payout.Status.PENDING)

    def test_failed_to_completed_blocked(self):
        """Terminal state: failed cannot transition to completed."""
        p = self._make_payout()
        p.transition_to(Payout.Status.PROCESSING)
        p.transition_to(Payout.Status.FAILED)
        with self.assertRaises(ValueError):
            p.transition_to(Payout.Status.COMPLETED)

    def test_pending_to_completed_blocked(self):
        """Cannot skip processing state."""
        p = self._make_payout()
        with self.assertRaises(ValueError):
            p.transition_to(Payout.Status.COMPLETED)


class IdempotencyTest(TransactionTestCase):
    """
    Idempotency: same Idempotency-Key returns the same response.
    
    Uses TransactionTestCase because we need real DB transactions
    (TestCase wraps everything in a transaction that never commits,
    which breaks SELECT FOR UPDATE behavior).
    """

    def setUp(self):
        self.client = APIClient()
        self.merchant, self.bank = _setup_merchant(
            "Idempotency Merchant", "idempotent@test.com", 500_000
        )
        self.url = f'/api/v1/merchants/{self.merchant.id}/payouts/create/'
        self.idempotency_key = str(uuid.uuid4())

    def test_idempotency_same_key_returns_same_response(self):
        """
        Two calls with the same Idempotency-Key must return identical responses
        and only create one payout.
        """
        payload = {
            'amount_paise': 10_000,
            'bank_account_id': str(self.bank.id),
        }
        headers = {'HTTP_IDEMPOTENCY_KEY': self.idempotency_key}

        # First call
        response1 = self.client.post(
            self.url, payload, format='json', **headers
        )
        self.assertEqual(response1.status_code, status.HTTP_201_CREATED)

        # Second call — same key
        response2 = self.client.post(
            self.url, payload, format='json', **headers
        )
        self.assertEqual(response2.status_code, status.HTTP_201_CREATED)

        # Responses must be identical
        self.assertEqual(response1.data, response2.data)

        # Only one payout should exist
        payout_count = Payout.objects.filter(merchant=self.merchant).count()
        self.assertEqual(payout_count, 1)

        # Only one idempotency key record
        key_count = IdempotencyKey.objects.filter(
            merchant=self.merchant, key=self.idempotency_key
        ).count()
        self.assertEqual(key_count, 1)

    def test_different_keys_create_different_payouts(self):
        """Different keys should create separate payouts."""
        payload = {'amount_paise': 10_000, 'bank_account_id': str(self.bank.id)}

        r1 = self.client.post(self.url, payload, format='json',
                              HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()))
        r2 = self.client.post(self.url, payload, format='json',
                              HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()))

        self.assertEqual(r1.status_code, status.HTTP_201_CREATED)
        self.assertEqual(r2.status_code, status.HTTP_201_CREATED)
        self.assertNotEqual(r1.data['payout']['id'], r2.data['payout']['id'])

    def test_missing_idempotency_key_returns_400(self):
        payload = {'amount_paise': 10_000, 'bank_account_id': str(self.bank.id)}
        response = self.client.post(self.url, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('Idempotency-Key', response.data['error'])

    def test_insufficient_balance_idempotent(self):
        """A failed (422) response is also idempotent."""
        payload = {'amount_paise': 999_999_999, 'bank_account_id': str(self.bank.id)}
        key = str(uuid.uuid4())
        headers = {'HTTP_IDEMPOTENCY_KEY': key}

        r1 = self.client.post(self.url, payload, format='json', **headers)
        r2 = self.client.post(self.url, payload, format='json', **headers)

        self.assertEqual(r1.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(r2.status_code, r1.status_code)
        self.assertEqual(r2.data, r1.data)


class ConcurrencyTest(TransactionTestCase):
    """
    Concurrency: two simultaneous payout requests that would overdraw the balance
    must result in exactly one success and one failure.

    This test uses threads to simulate concurrent requests. Because we use
    SELECT FOR UPDATE, the DB serializes the two transactions — one will
    acquire the lock and check the balance; the other waits and then finds
    insufficient funds after the first commits.

    Uses TransactionTestCase so each thread gets a real, independent transaction.
    """

    def setUp(self):
        self.merchant, self.bank = _setup_merchant(
            "Concurrency Merchant", "concurrency@test.com", 10_000  # 100 INR
        )
        self.url = f'/api/v1/merchants/{self.merchant.id}/payouts/create/'

    def test_concurrent_payout_overdraw_one_succeeds(self):
        """
        Two simultaneous requests for 60 INR each on a 100 INR balance.
        Exactly one must succeed (201), the other must be rejected (422).
        """
        results = []
        errors = []

        def make_request(key):
            try:
                client = APIClient()
                response = client.post(
                    self.url,
                    {'amount_paise': 6_000, 'bank_account_id': str(self.bank.id)},
                    format='json',
                    HTTP_IDEMPOTENCY_KEY=key,
                )
                results.append(response.status_code)
            except Exception as e:
                errors.append(str(e))

        key1 = str(uuid.uuid4())
        key2 = str(uuid.uuid4())

        t1 = threading.Thread(target=make_request, args=(key1,))
        t2 = threading.Thread(target=make_request, args=(key2,))

        t1.start()
        t2.start()
        t1.join()
        t2.join()

        self.assertEqual(len(errors), 0, f"Unexpected errors: {errors}")
        self.assertEqual(len(results), 2)

        success_count = results.count(status.HTTP_201_CREATED)
        failure_count = results.count(status.HTTP_422_UNPROCESSABLE_ENTITY)

        self.assertEqual(
            success_count, 1,
            f"Expected exactly 1 success, got: {results}"
        )
        self.assertEqual(
            failure_count, 1,
            f"Expected exactly 1 failure (422), got: {results}"
        )

        # Only one payout created
        payout_count = Payout.objects.filter(merchant=self.merchant).count()
        self.assertEqual(payout_count, 1)

    def test_balance_integrity_after_concurrent_requests(self):
        """
        After concurrent requests, the balance derived from the ledger must match
        the sum of credits minus debits. No phantom balance created.
        """
        keys = [str(uuid.uuid4()) for _ in range(5)]
        results = []

        def make_request(key):
            client = APIClient()
            r = client.post(
                self.url,
                {'amount_paise': 3_000, 'bank_account_id': str(self.bank.id)},
                format='json',
                HTTP_IDEMPOTENCY_KEY=key,
            )
            results.append(r.status_code)

        threads = [threading.Thread(target=make_request, args=(k,)) for k in keys]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # At most floor(10000 / 3000) = 3 payouts can succeed
        success_count = results.count(status.HTTP_201_CREATED)
        self.assertLessEqual(success_count, 3)

        # The held balance should equal success_count * 3000
        merchant = Merchant.objects.get(id=self.merchant.id)
        held = merchant.get_held_balance()
        self.assertEqual(held, success_count * 3_000)


class PayoutLifecycleTest(TransactionTestCase):
    """Integration tests for payout lifecycle via background task."""

    def setUp(self):
        self.merchant, self.bank = _setup_merchant(
            "Lifecycle Merchant", "lifecycle@test.com", 100_000
        )

    def _create_processing_payout(self, amount_paise):
        payout = Payout.objects.create(
            merchant=self.merchant,
            bank_account=self.bank,
            amount_paise=amount_paise,
            status=Payout.Status.PROCESSING,
            attempt_count=1,
        )
        return payout

    def test_completed_payout_creates_debit_ledger_entry(self):
        payout = self._create_processing_payout(50_000)
        _finalize_payout(str(payout.id), OUTCOME_SUCCESS)

        payout.refresh_from_db()
        self.assertEqual(payout.status, Payout.Status.COMPLETED)

        # Debit entry must exist
        debit = LedgerEntry.objects.get(payout=payout, entry_type=LedgerEntry.EntryType.DEBIT)
        self.assertEqual(debit.amount_paise, -50_000)

        # Balance should be 100000 - 50000 = 50000
        self.assertEqual(self.merchant.get_balance(), 50_000)

    def test_failed_payout_releases_held_funds(self):
        payout = self._create_processing_payout(50_000)

        # Held balance should include the payout
        self.assertEqual(self.merchant.get_held_balance(), 50_000)

        _finalize_payout(str(payout.id), OUTCOME_FAILURE)

        payout.refresh_from_db()
        self.assertEqual(payout.status, Payout.Status.FAILED)

        # No debit entry created
        debit_count = LedgerEntry.objects.filter(
            payout=payout, entry_type=LedgerEntry.EntryType.DEBIT
        ).count()
        self.assertEqual(debit_count, 0)

        # Full balance restored
        self.assertEqual(self.merchant.get_balance(), 100_000)
        self.assertEqual(self.merchant.get_held_balance(), 0)
