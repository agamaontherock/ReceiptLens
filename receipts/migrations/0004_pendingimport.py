from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('receipts', '0003_receipt_qr_url'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='PendingImport',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('qr_url', models.URLField(max_length=512)),
                ('status', models.CharField(
                    choices=[
                        ('pending', 'Очікує'),
                        ('processing', 'Обробляється'),
                        ('processed', 'Оброблено'),
                        ('failed', 'Помилка'),
                    ],
                    db_index=True,
                    default='pending',
                    max_length=16,
                )),
                ('retry_count', models.IntegerField(default=0)),
                ('next_retry_at', models.DateTimeField(blank=True, db_index=True, null=True)),
                ('last_error', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('user', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='pending_imports',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('receipt', models.OneToOneField(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='pending_import',
                    to='receipts.receipt',
                )),
            ],
        ),
    ]
