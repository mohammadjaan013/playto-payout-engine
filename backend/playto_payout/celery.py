import os
from celery import Celery

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'playto_payout.settings')

app = Celery('playto_payout')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()
