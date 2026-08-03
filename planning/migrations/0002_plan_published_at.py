from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('planning', '0001_planning_schema_chunk5'),
    ]

    operations = [
        migrations.AddField(
            model_name='plan',
            name='published_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
