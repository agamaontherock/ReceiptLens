import os
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Create a superuser from DJANGO_SUPERUSER_EMAIL / DJANGO_SUPERUSER_PASSWORD env vars if one does not exist"

    def handle(self, *args, **options):
        email = os.environ.get("DJANGO_SUPERUSER_EMAIL", "").strip()
        password = os.environ.get("DJANGO_SUPERUSER_PASSWORD", "").strip()

        if not email or not password:
            self.stdout.write("DJANGO_SUPERUSER_EMAIL or DJANGO_SUPERUSER_PASSWORD not set — skipping.")
            return

        User = get_user_model()
        user = User.objects.filter(email__iexact=email).first()
        if user:
            if user.is_superuser and user.is_staff:
                self.stdout.write(f"Superuser {email} already exists — skipping.")
                return
            user.is_staff = True
            user.is_superuser = True
            user.set_password(password)
            user.save(update_fields=["is_staff", "is_superuser", "password"])
            self.stdout.write(self.style.SUCCESS(f"Existing user {email} promoted to superuser."))
            return

        User.objects.create_superuser(username=email, email=email, password=password)
        self.stdout.write(self.style.SUCCESS(f"Superuser {email} created."))
