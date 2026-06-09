from django.urls import path
from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("scan/", views.scan, name="scan"),
    path("receipts/parse/", views.receipts_parse, name="receipts_parse"),
    path("receipts/save/", views.receipts_save, name="receipts_save"),
    path("receipts/<int:pk>/", views.receipt_detail, name="receipt_detail"),
    path("receipts/<int:pk>/edit/", views.receipt_edit, name="receipt_edit"),
    path("receipts/<int:pk>/delete/", views.receipt_delete, name="receipt_delete"),
    path("pending-imports/", views.pending_imports, name="pending_imports"),
    path("pending-imports/save/", views.pending_import_save, name="pending_import_save"),
    path("pending-imports/<int:pk>/retry/", views.pending_import_retry, name="pending_import_retry"),
    path("pending-imports/<int:pk>/delete/", views.pending_import_delete, name="pending_import_delete"),
    path("manual/", views.manual, name="manual"),
    path("analytics/", views.analytics, name="analytics"),
    path("profile/", views.profile, name="profile"),
    path("auth/register/", views.register, name="register"),
    path("auth/login/", views.login_view, name="login"),
    path("auth/logout/", views.logout_view, name="logout"),
]
