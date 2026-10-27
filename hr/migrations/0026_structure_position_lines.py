import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('hr', '0025_mfa_two_step_login'),
    ]

    operations = [
        migrations.AddField(
            model_name='structureposition',
            name='reports_to',
            field=models.ForeignKey(blank=True, help_text='The position it is drawn under, in the same division or section. Empty: directly under its division or section.', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='reports', to='hr.structureposition'),
        ),
        migrations.AddField(
            model_name='structureposition',
            name='joins_row',
            field=models.BooleanField(default=False, help_text='Drawn under the row of positions above it, joined to the bottom of each.'),
        ),
    ]
