from django.urls import path
from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("scan/", views.scan, name="scan"),
    path("receipts/parse/", views.receipts_parse, name="receipts_parse"),
    path("receipts/save/", views.receipts_save, name="receipts_save"),
    path("receipts/<int:pk>/", views.receipt_detail, name="receipt_detail"),
    path("receipts/<int:pk>/delete/", views.receipt_delete, name="receipt_delete"),
    path("manual/", views.manual, name="manual"),
    path("analytics/", views.analytics, name="analytics"),
]
