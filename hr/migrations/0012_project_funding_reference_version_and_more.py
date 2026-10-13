from django.db import migrations, models
from django.utils import timezone


class Migration(migrations.Migration):

    dependencies = [
        ("hr", "0011_reapply_app_role_audit_privileges"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="project",
            options={
                "ordering": ["-start_date", "name"],
                "permissions": [("manage_projects", "Can create and edit LGU projects")],
            },
        ),
        migrations.AddField(
            model_name="project",
            name="funding_reference",
            field=models.CharField(
                blank=True,
                help_text="Certificate or obligation number from Budget / Accounting.",
                max_length=100,
                verbose_name="Funding reference",
            ),
        ),
        migrations.AddField(
            model_name="project",
            name="updated_at",
            field=models.DateTimeField(auto_now=True, default=timezone.now),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="project",
            name="version",
            field=models.PositiveIntegerField(default=1),
        ),
    ]
