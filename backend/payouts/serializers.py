from rest_framework import serializers
from .models import Merchant, BankAccount, LedgerEntry, Payout, IdempotencyKey


class BankAccountSerializer(serializers.ModelSerializer):
    class Meta:
        model = BankAccount
        fields = ['id', 'account_number', 'ifsc_code', 'account_holder_name', 'is_primary']


class LedgerEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = LedgerEntry
        fields = ['id', 'entry_type', 'amount_paise', 'description', 'payout', 'created_at']


class PayoutSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payout
        fields = [
            'id', 'merchant', 'bank_account', 'amount_paise', 'status',
            'attempt_count', 'created_at', 'updated_at',
            'processing_started_at', 'completed_at', 'failure_reason',
        ]
        read_only_fields = [
            'id', 'merchant', 'status', 'attempt_count',
            'created_at', 'updated_at', 'processing_started_at',
            'completed_at', 'failure_reason',
        ]


class CreatePayoutSerializer(serializers.Serializer):
    amount_paise = serializers.IntegerField(min_value=1)
    bank_account_id = serializers.UUIDField()

    def validate_amount_paise(self, value):
        # Minimum payout: 1 INR (100 paise)
        if value < 100:
            raise serializers.ValidationError("Minimum payout amount is 100 paise (1 INR).")
        return value


class MerchantDashboardSerializer(serializers.ModelSerializer):
    available_balance_paise = serializers.SerializerMethodField()
    held_balance_paise = serializers.SerializerMethodField()
    bank_accounts = BankAccountSerializer(many=True, read_only=True)

    class Meta:
        model = Merchant
        fields = [
            'id', 'name', 'email',
            'available_balance_paise', 'held_balance_paise',
            'bank_accounts',
        ]

    def get_available_balance_paise(self, obj):
        return obj.get_balance()

    def get_held_balance_paise(self, obj):
        return obj.get_held_balance()
