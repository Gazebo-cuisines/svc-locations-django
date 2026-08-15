from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name='ErrorTicket',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('fingerprint', models.CharField(max_length=32, unique=True)),
                ('status', models.CharField(choices=[('open', 'Open'), ('investigating', 'Investigating'), ('resolved', 'Resolved')], db_index=True, default='open', max_length=16)),
                ('note', models.TextField(blank=True, default='')),
                ('message', models.CharField(max_length=1024)),
                ('stack', models.TextField(blank=True, default='')),
                ('url', models.CharField(blank=True, default='', max_length=512)),
                ('source', models.CharField(choices=[('client', 'Client'), ('server', 'Server')], default='client', max_length=16)),
                ('occurrences', models.PositiveIntegerField(default=1)),
                ('actor_sub', models.CharField(blank=True, default='', max_length=128)),
                ('actor_username', models.CharField(blank=True, default='', max_length=128)),
                ('payload', models.JSONField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('last_seen_at', models.DateTimeField()),
            ],
            options={
                'db_table': 'ops_error_ticket',
                'ordering': ['-last_seen_at'],
            },
        ),
        migrations.AddIndex(
            model_name='errorticket',
            index=models.Index(fields=['status', '-last_seen_at'], name='idx_ops_err_status_seen'),
        ),
    ]
