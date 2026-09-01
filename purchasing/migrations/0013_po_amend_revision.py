from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('purchasing', '0012_qc_editor_lock'),
    ]

    operations = [
        migrations.AddField(
            model_name='purchaseorder',
            name='revision_no',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AlterField(
            model_name='purchaseorderhistory',
            name='event_type',
            field=models.CharField(
                choices=[
                    ('create', 'Create'),
                    ('update', 'Update'),
                    ('amend', 'Amend'),
                    ('accept', 'Accept'),
                    ('reject', 'Reject'),
                    ('non_conformance', 'Non-conformance'),
                    ('note', 'Note'),
                ],
                max_length=32,
            ),
        ),
    ]
