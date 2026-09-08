from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0003_maintenance_notice'),
    ]

    operations = [
        migrations.CreateModel(
            name='AiCase',
            fields=[
                (
                    'id',
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name='ID',
                    ),
                ),
                ('session_id', models.CharField(max_length=64, unique=True)),
                ('opened_by_user_id', models.IntegerField(blank=True, null=True)),
                (
                    'opened_by_username',
                    models.CharField(blank=True, default='', max_length=128),
                ),
                (
                    'opened_by_name',
                    models.CharField(blank=True, default='', max_length=128),
                ),
                ('product_id', models.IntegerField(blank=True, null=True)),
                (
                    'recipe_code',
                    models.CharField(blank=True, default='', max_length=128),
                ),
                ('bag_code', models.CharField(blank=True, default='', max_length=32)),
                ('turns', models.JSONField(default=list)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'db_table': 'ai_case',
                'ordering': ['-updated_at'],
            },
        ),
        migrations.AddIndex(
            model_name='aicase',
            index=models.Index(
                fields=['opened_by_user_id', '-updated_at'],
                name='idx_ai_case_actor',
            ),
        ),
        migrations.AddIndex(
            model_name='aicase',
            index=models.Index(
                fields=['product_id', '-updated_at'],
                name='idx_ai_case_product',
            ),
        ),
    ]
