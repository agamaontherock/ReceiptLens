from django.conf import settings


def google_oauth(request):
    return {"GOOGLE_OAUTH_ENABLED": getattr(settings, "GOOGLE_OAUTH_ENABLED", False)}
