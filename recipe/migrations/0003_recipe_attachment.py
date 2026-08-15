from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('recipe', '0002_recipe_approval_fields'),
    ]

    operations = [
        migrations.CreateModel(
            name='RecipeAttachment',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('kind', models.CharField(
                    choices=[
                        ('hero', 'Finished product'),
                        ('step', 'Process step'),
                        ('packaging', 'Packaging'),
                        ('other', 'Other'),
                    ],
                    default='step',
                    max_length=32,
                )),
                ('s3_key', models.CharField(max_length=512)),
                ('content_type', models.CharField(blank=True, max_length=64, null=True)),
                ('original_filename', models.CharField(blank=True, max_length=255, null=True)),
                ('caption', models.CharField(blank=True, max_length=255, null=True)),
                ('sort_order', models.IntegerField(default=0)),
                ('uploaded_by_sub', models.CharField(blank=True, max_length=64, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('component', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='attachments',
                    to='recipe.recipecomponent',
                )),
                ('recipe_version', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='attachments',
                    to='recipe.recipeversion',
                )),
            ],
            options={
                'db_table': 'recipe_attachment',
                'ordering': ['sort_order', 'id'],
            },
        ),
    ]
