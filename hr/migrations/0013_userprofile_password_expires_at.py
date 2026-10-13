from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("hr", "0012_project_funding_reference_version_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="password_expires_at",
            field=models.DateTimeField(
                blank=True,
                help_text="When an unused one-time password stops working (DECISIONS A4).",
                null=True,
            ),
        ),
    ]
