import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("hr", "0013_userprofile_password_expires_at"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="CalendarEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=200)),
                (
                    "kind",
                    models.CharField(
                        choices=[
                            ("interview", "Hiring interview"),
                            ("deadline", "Deadline"),
                            ("holiday", "Holiday / no work"),
                            ("meeting", "Meeting / activity"),
                            ("other", "Other"),
                        ],
                        max_length=20,
                    ),
                ),
                ("start_date", models.DateField()),
                ("end_date", models.DateField(blank=True, help_text="Leave blank for a one-day event.", null=True)),
                ("time", models.TimeField(blank=True, null=True)),
                ("notes", models.TextField(blank=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "office",
                    models.ForeignKey(
                        blank=True,
                        help_text="Only this office (and all-office roles) see it. Leave blank to show to everyone.",
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="calendar_events",
                        to="hr.office",
                    ),
                ),
            ],
            options={
                "ordering": ["start_date", "time"],
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(("end_date__isnull", True), ("end_date__gte", models.F("start_date")), _connector="OR"),
                        name="calendarevent_end_after_start",
                        violation_error_message="End date cannot be before start date.",
                    )
                ],
            },
        ),
    ]
