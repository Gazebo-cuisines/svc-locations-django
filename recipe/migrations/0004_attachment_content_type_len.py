from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('recipe', '0003_recipe_attachment'),
    ]

    operations = [
        migrations.AlterField(
            model_name='recipeattachment',
            name='content_type',
            field=models.CharField(blank=True, max_length=128, null=True),
        ),
    ]
