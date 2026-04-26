from django.urls import path
from . import views

urlpatterns = [
    # Merchant endpoints
    path('merchants/', views.list_merchants, name='list-merchants'),
    path('merchants/<uuid:merchant_id>/', views.merchant_dashboard, name='merchant-dashboard'),
    path('merchants/<uuid:merchant_id>/ledger/', views.merchant_ledger, name='merchant-ledger'),
    path('merchants/<uuid:merchant_id>/payouts/', views.merchant_payouts, name='merchant-payouts'),

    # Payout endpoints
    path('merchants/<uuid:merchant_id>/payouts/create/', views.create_payout, name='create-payout'),
    path('merchants/<uuid:merchant_id>/payouts/<uuid:payout_id>/', views.payout_detail, name='payout-detail'),
]
