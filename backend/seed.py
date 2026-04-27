"""
Seed script for Playto Payout Engine.

Creates 3 merchants with bank accounts and credit history.
Run: python manage.py shell < seed.py
  or: python manage.py seed_data (if management command is used)
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'playto_payout.settings')
django.setup()

from payouts.models import Merchant, BankAccount, LedgerEntry, Payout, IdempotencyKey

print("Seeding database...")

# Skip seeding if data already exists (idempotent on Railway re-deploys)
if Merchant.objects.exists():
    print("Data already seeded — skipping.")
    exit(0)

# Merchant 1: Arjun's design agency
arjun = Merchant.objects.create(
    name="Arjun Sharma Design Co.",
    email="arjun@arjundesign.in",
)
arjun_bank = BankAccount.objects.create(
    merchant=arjun,
    account_number="00112233445566",
    ifsc_code="HDFC0001234",
    account_holder_name="Arjun Sharma",
    is_primary=True,
)
# Credits: received payments from 3 clients
LedgerEntry.objects.create(
    merchant=arjun,
    entry_type=LedgerEntry.EntryType.CREDIT,
    amount_paise=250_000,  # INR 2,500
    description="Client payment: Acme Corp - Logo Design",
)
LedgerEntry.objects.create(
    merchant=arjun,
    entry_type=LedgerEntry.EntryType.CREDIT,
    amount_paise=500_000,  # INR 5,000
    description="Client payment: TechStart Inc - Brand Identity",
)
LedgerEntry.objects.create(
    merchant=arjun,
    entry_type=LedgerEntry.EntryType.CREDIT,
    amount_paise=175_000,  # INR 1,750
    description="Client payment: GlobalRetail - Banner Set",
)

# Merchant 2: Priya's content studio
priya = Merchant.objects.create(
    name="Priya Nair Content Studio",
    email="priya@priyacontent.io",
)
priya_bank = BankAccount.objects.create(
    merchant=priya,
    account_number="99887766554433",
    ifsc_code="ICIC0005678",
    account_holder_name="Priya Nair",
    is_primary=True,
)
LedgerEntry.objects.create(
    merchant=priya,
    entry_type=LedgerEntry.EntryType.CREDIT,
    amount_paise=1_000_000,  # INR 10,000
    description="Client payment: ContentFirst USA - Monthly retainer",
)
LedgerEntry.objects.create(
    merchant=priya,
    entry_type=LedgerEntry.EntryType.CREDIT,
    amount_paise=350_000,  # INR 3,500
    description="Client payment: BlogPros Ltd - SEO articles",
)
# Priya already did one payout previously
LedgerEntry.objects.create(
    merchant=priya,
    entry_type=LedgerEntry.EntryType.DEBIT,
    amount_paise=-400_000,  # INR 4,000 withdrawn
    description="Payout to bank account ...4433",
)

# Merchant 3: Rahul's dev freelancer
rahul = Merchant.objects.create(
    name="Rahul Verma Dev",
    email="rahul@rahulverma.dev",
)
rahul_bank = BankAccount.objects.create(
    merchant=rahul,
    account_number="11223344556677",
    ifsc_code="SBIN0009012",
    account_holder_name="Rahul Verma",
    is_primary=True,
)
BankAccount.objects.create(
    merchant=rahul,
    account_number="77665544332211",
    ifsc_code="AXIS0003456",
    account_holder_name="Rahul Verma",
    is_primary=False,
)
LedgerEntry.objects.create(
    merchant=rahul,
    entry_type=LedgerEntry.EntryType.CREDIT,
    amount_paise=2_500_000,  # INR 25,000
    description="Client payment: SiliconValley Startup - Backend API v1",
)
LedgerEntry.objects.create(
    merchant=rahul,
    entry_type=LedgerEntry.EntryType.CREDIT,
    amount_paise=1_500_000,  # INR 15,000
    description="Client payment: EuropeCommerce - Shopify integration",
)
LedgerEntry.objects.create(
    merchant=rahul,
    entry_type=LedgerEntry.EntryType.DEBIT,
    amount_paise=-1_000_000,  # INR 10,000 withdrawn
    description="Payout to bank account ...6677",
)

print("\n✓ Seed complete!")
print(f"\n  Arjun Sharma Design Co.  | ID: {arjun.id}")
print(f"    Balance: INR {arjun.get_balance() / 100:.2f}")
print(f"\n  Priya Nair Content Studio | ID: {priya.id}")
print(f"    Balance: INR {priya.get_balance() / 100:.2f}")
print(f"\n  Rahul Verma Dev           | ID: {rahul.id}")
print(f"    Balance: INR {rahul.get_balance() / 100:.2f}")
print("\nUse these merchant IDs in the frontend or API calls.")
