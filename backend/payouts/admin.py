from django.contrib import admin
from .models import Merchant, BankAccount, LedgerEntry, Payout, IdempotencyKey


@admin.register(Merchant)
class MerchantAdmin(admin.ModelAdmin):
    list_display = ['name', 'email', 'created_at']
    search_fields = ['name', 'email']


@admin.register(BankAccount)
class BankAccountAdmin(admin.ModelAdmin):
    list_display = ['merchant', 'account_holder_name', 'account_number', 'ifsc_code', 'is_primary']
    list_filter = ['merchant']


@admin.register(LedgerEntry)
class LedgerEntryAdmin(admin.ModelAdmin):
    list_display = ['merchant', 'entry_type', 'amount_paise', 'description', 'created_at']
    list_filter = ['entry_type', 'merchant']
    readonly_fields = ['id', 'created_at']


@admin.register(Payout)
class PayoutAdmin(admin.ModelAdmin):
    list_display = ['id', 'merchant', 'amount_paise', 'status', 'attempt_count', 'created_at']
    list_filter = ['status', 'merchant']
    readonly_fields = ['id', 'created_at', 'updated_at']


@admin.register(IdempotencyKey)
class IdempotencyKeyAdmin(admin.ModelAdmin):
    list_display = ['merchant', 'key', 'response_status_code', 'created_at']
    list_filter = ['merchant']
    readonly_fields = ['id', 'created_at']
