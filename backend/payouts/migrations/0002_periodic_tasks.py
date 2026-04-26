"""
Add Celery Beat periodic task for stuck payout checker.
Runs every 10 seconds.
"""
from django.db import migrations


def add_periodic_task(apps, schema_editor):
    try:
        IntervalSchedule = apps.get_model('django_celery_beat', 'IntervalSchedule')
        PeriodicTask = apps.get_model('django_celery_beat', 'PeriodicTask')

        schedule, _ = IntervalSchedule.objects.get_or_create(
            every=10,
            period='seconds',  # use literal — historical model has no class constants
        )
        PeriodicTask.objects.get_or_create(
            name='Check stuck payouts',
            defaults={
                'task': 'payouts.check_stuck_payouts',
                'interval': schedule,
                'enabled': True,
            },
        )
    except Exception:
        # If django_celery_beat tables don't exist yet, skip gracefully
        pass


def remove_periodic_task(apps, schema_editor):
    try:
        PeriodicTask = apps.get_model('django_celery_beat', 'PeriodicTask')
        PeriodicTask.objects.filter(name='Check stuck payouts').delete()
    except Exception:
        pass


class Migration(migrations.Migration):

    dependencies = [
        ('payouts', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(add_periodic_task, remove_periodic_task),
    ]
