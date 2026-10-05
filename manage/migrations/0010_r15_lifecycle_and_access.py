import time

from django.db import migrations, models


def retain_recent_failures(apps, schema_editor):
    # The old fixed window did not store individual timestamps. Preserve its
    # still-active count conservatively for at most another five minutes.
    # Existing lock deadlines are never changed. Do not fabricate class endings.
    now = time.time()
    for name in ('OwnerLoginGuard', 'CommitteeLoginGuard'):
        model = apps.get_model('manage', name)
        for guard in model.objects.using(schema_editor.connection.alias).all().iterator():
            if guard.failures and guard.first_failure_at > now - 300:
                guard.failure_times = [now] * min(guard.failures, 8)
                guard.save(update_fields=['failure_times'])


class Migration(migrations.Migration):
    dependencies = [('manage', '0009_current_class_report')]
    operations = [
        migrations.AddField('class', 'ended_at', models.DateTimeField(blank=True, null=True)),
        migrations.AddField('class', 'ended_term_key', models.CharField(blank=True, default='', max_length=20)),
        migrations.AddField('ownerloginguard', 'failure_times', models.JSONField(blank=True, default=list)),
        migrations.AddField('committeeloginguard', 'failure_times', models.JSONField(blank=True, default=list)),
        migrations.AddField('publicclassreportlink', 'revision', models.PositiveIntegerField(default=1)),
        migrations.RunPython(retain_recent_failures, migrations.RunPython.noop),
    ]
