from decimal import Decimal

from django.db import models


class RecipeVersionStatus(models.TextChoices):
    DRAFT = 'draft', 'Draft'
    APPROVED = 'approved', 'Approved'
    ACTIVE = 'active', 'Active'
    RETIRED = 'retired', 'Retired'


class Recipe(models.Model):
    """One formula book per product; versions live under it."""

    id = models.AutoField(primary_key=True)
    product = models.OneToOneField(
        'product.Product',
        on_delete=models.PROTECT,
        related_name='recipe',
    )
    name = models.CharField(max_length=128, null=True, blank=True)
    remarks = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'recipe'

    def __str__(self):
        return f'recipe:{self.id}:product:{self.product_id}'


class RecipeVersion(models.Model):
    """Versioned recipe snapshot. One-active enforced in service layer (chunk 4)."""

    id = models.AutoField(primary_key=True)
    recipe = models.ForeignKey(
        Recipe,
        on_delete=models.PROTECT,
        related_name='versions',
    )
    version_number = models.IntegerField()
    status = models.CharField(
        max_length=16,
        choices=RecipeVersionStatus.choices,
        default=RecipeVersionStatus.DRAFT,
    )
    process_loss = models.DecimalField(
        max_digits=10, decimal_places=4, default=Decimal('1.0000'),
    )
    batch_quantity = models.DecimalField(
        max_digits=16, decimal_places=6, null=True, blank=True,
    )
    batch_unit = models.ForeignKey(
        'product.Unit',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='recipe_version_batches',
    )
    sum_batch_quantity = models.DecimalField(
        max_digits=16, decimal_places=6, null=True, blank=True,
    )
    sum_net_quantity = models.DecimalField(
        max_digits=16, decimal_places=6, null=True, blank=True,
    )
    sum_gross_quantity = models.DecimalField(
        max_digits=16, decimal_places=6, null=True, blank=True,
    )
    location = models.ForeignKey(
        'locations.Location',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='recipe_versions',
    )
    effective_from = models.DateField(null=True, blank=True)
    effective_to = models.DateField(null=True, blank=True)
    remarks = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'recipe_version'
        constraints = [
            models.UniqueConstraint(
                fields=['recipe', 'version_number'],
                name='uq_recipe_version_number',
            ),
            models.CheckConstraint(
                check=models.Q(
                    status__in=[
                        RecipeVersionStatus.DRAFT,
                        RecipeVersionStatus.APPROVED,
                        RecipeVersionStatus.ACTIVE,
                        RecipeVersionStatus.RETIRED,
                    ]
                ),
                name='chk_recipe_version_status',
            ),
            models.CheckConstraint(
                check=models.Q(process_loss__gt=0),
                name='chk_recipe_version_process_loss',
            ),
        ]
        indexes = [
            models.Index(
                fields=['recipe', 'status'],
                name='idx_recipe_version_status',
            ),
        ]

    def __str__(self):
        return f'recipe_version:{self.id}:v{self.version_number}:{self.status}'


class RecipeComponent(models.Model):
    """Component line on a recipe version. Yield/cost SoT stays on product_* tables."""

    id = models.AutoField(primary_key=True)
    recipe_version = models.ForeignKey(
        RecipeVersion,
        on_delete=models.CASCADE,
        related_name='components',
    )
    line_no = models.IntegerField()
    component_product = models.ForeignKey(
        'product.Product',
        on_delete=models.PROTECT,
        related_name='used_in_recipe_components',
    )
    quantity = models.DecimalField(max_digits=16, decimal_places=6)
    unit = models.ForeignKey(
        'product.Unit',
        on_delete=models.PROTECT,
        related_name='recipe_components',
    )
    batch_quantity = models.DecimalField(
        max_digits=16, decimal_places=6, null=True, blank=True,
    )
    gross_batch_quantity = models.DecimalField(
        max_digits=16, decimal_places=6, null=True, blank=True,
    )
    step_instructions = models.TextField(null=True, blank=True)
    is_implicit = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'recipe_component'
        constraints = [
            models.UniqueConstraint(
                fields=['recipe_version', 'component_product'],
                name='uq_version_component',
            ),
            models.UniqueConstraint(
                fields=['recipe_version', 'line_no'],
                name='uq_version_line_no',
            ),
            models.CheckConstraint(
                check=models.Q(quantity__gt=0),
                name='chk_recipe_component_qty',
            ),
        ]
        indexes = [
            models.Index(
                fields=['component_product'],
                name='idx_component_product',
            ),
        ]

    def __str__(self):
        return (
            f'recipe_component:{self.id}:'
            f'v{self.recipe_version_id}:line{self.line_no}'
        )
