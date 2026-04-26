import uuid
import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name='Merchant',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=255)),
                ('email', models.EmailField(max_length=254, unique=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'ordering': ['name'],
            },
        ),
        migrations.CreateModel(
            name='BankAccount',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('account_number', models.CharField(max_length=20)),
                ('ifsc_code', models.CharField(max_length=11)),
                ('account_holder_name', models.CharField(max_length=255)),
                ('is_primary', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('merchant', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='bank_accounts',
                    to='payouts.merchant',
                )),
            ],
            options={
                'ordering': ['-is_primary', 'created_at'],
            },
        ),
        migrations.CreateModel(
            name='Payout',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('amount_paise', models.BigIntegerField()),
                ('status', models.CharField(
                    choices=[
                        ('pending', 'Pending'),
                        ('processing', 'Processing'),
                        ('completed', 'Completed'),
                        ('failed', 'Failed'),
                    ],
                    db_index=True,
                    default='pending',
                    max_length=20,
                )),
                ('attempt_count', models.PositiveSmallIntegerField(default=0)),
                ('max_attempts', models.PositiveSmallIntegerField(default=3)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('processing_started_at', models.DateTimeField(blank=True, null=True)),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                ('failure_reason', models.CharField(blank=True, max_length=500)),
                ('bank_account', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='payouts',
                    to='payouts.bankaccount',
                )),
                ('merchant', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='payouts',
                    to='payouts.merchant',
                )),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='LedgerEntry',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('entry_type', models.CharField(
                    choices=[('credit', 'Credit'), ('debit', 'Debit')],
                    max_length=10,
                )),
                ('amount_paise', models.BigIntegerField()),
                ('description', models.CharField(max_length=500)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('merchant', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='ledger_entries',
                    to='payouts.merchant',
                )),
                ('payout', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='ledger_entries',
                    to='payouts.payout',
                )),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='IdempotencyKey',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('key', models.CharField(db_index=True, max_length=255)),
                ('response_status_code', models.PositiveSmallIntegerField()),
                ('response_body', models.JSONField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('merchant', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='idempotency_keys',
                    to='payouts.merchant',
                )),
                ('payout', models.OneToOneField(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='idempotency_key_record',
                    to='payouts.payout',
                )),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.AlterUniqueTogether(
            name='idempotencykey',
            unique_together={('merchant', 'key')},
        ),
    ]
