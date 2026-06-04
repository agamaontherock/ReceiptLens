from django.apps import AppConfig


class ReceiptsConfig(AppConfig):
    name = "receipts"

    def ready(self):
        from django.conf import settings
        from django.db.models.signals import post_save
        from django.dispatch import receiver

        User = __import__("django.contrib.auth", fromlist=["get_user_model"]).get_user_model()

        @receiver(post_save, sender=User)
        def create_profile(sender, instance, created, **kwargs):
            if created:
                from receipts.models import UserProfile
                UserProfile.objects.get_or_create(user=instance)
