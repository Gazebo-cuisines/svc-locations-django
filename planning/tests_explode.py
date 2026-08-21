from datetime import date
from decimal import Decimal

from django.test import SimpleTestCase, TestCase

from locations.models import Location
from planning.models import Plan, PlanLine, PlanLineSource, PlanRequirement
from planning.services.explode import run_explode
from product.models import Category, Product, ProductClass, ProductYield, Range, Unit
from recipe.models import Recipe, RecipeComponent, RecipeVersion, RecipeVersionStatus
from recipe.utils import scaled_child_net


class ScaledChildNetTests(SimpleTestCase):
    def test_pack_per_unit(self):
        self.assertEqual(
            scaled_child_net(
                Decimal('5000'), Decimal('12'), bom_sum=Decimal('25'),
            ),
            Decimal('60000'),
        )

    def test_belt_piece_weight(self):
        self.assertEqual(
            scaled_child_net(
                Decimal('60000'), Decimal('80.2'), bom_sum=Decimal('81.2'),
            ),
            Decimal('4812000.0'),
        )

    def test_mixer_batch(self):
        got = scaled_child_net(
            Decimal('4812000'),
            Decimal('96000'),
            batch_quantity=Decimal('186080'),
            bom_sum=Decimal('186080'),
        )
        self.assertEqual(got, Decimal('4812000') * Decimal('96000') / Decimal('186080'))

    def test_spice_null_batch_uses_bom_sum(self):
        got = scaled_child_net(
            Decimal('285000'),
            Decimal('2000'),
            batch_quantity=None,
            bom_sum=Decimal('11040'),
        )
        self.assertEqual(got, Decimal('285000') * Decimal('2000') / Decimal('11040'))

    def test_min_batch_not_scaled(self):
        self.assertEqual(
            scaled_child_net(
                Decimal('50'),
                Decimal('2'),
                batch_quantity=Decimal('25'),
                bom_sum=Decimal('2'),
            ),
            Decimal('100'),
        )


class ExplodeBatchBomTests(TestCase):
    def setUp(self):
        ProductClass.objects.create(id=1, name='Finished')
        Category.objects.create(id=1, name='Meals')
        Range.objects.create(id=1, name='Main')
        self.unit = Unit.objects.create(id=1, name='grams')
        loc = Location.objects.create(id=1, name='Mixers', visible=True)
        self.mixer = self._product('Mixer', loc)
        self.potato = self._product('Potato', loc)
        ProductYield.objects.create(
            product=self.mixer, yield_factor=Decimal('1'), yield_factor_auto=Decimal('1'),
        )
        ProductYield.objects.create(
            product=self.potato, yield_factor=Decimal('1'), yield_factor_auto=Decimal('1'),
        )
        recipe = Recipe.objects.create(product=self.mixer, name='Mx')
        version = RecipeVersion.objects.create(
            recipe=recipe,
            version_number=1,
            status=RecipeVersionStatus.ACTIVE,
            process_loss=Decimal('1'),
            batch_quantity=Decimal('186080'),
        )
        RecipeComponent.objects.create(
            recipe_version=version,
            line_no=1,
            component_product=self.potato,
            quantity=Decimal('96000'),
            unit=self.unit,
        )
        self.plan = Plan.objects.create(plan_date=date(2026, 8, 21), location=loc)
        PlanLine.objects.create(
            plan=self.plan,
            product=self.mixer,
            quantity=Decimal('4812000'),
            unit=self.unit,
            source=PlanLineSource.MANUAL,
        )

    def _product(self, name, loc):
        return Product.objects.create(
            name=name,
            recipe_code=name[:8],
            product_class_id=1,
            category_id=1,
            range_id=1,
            unit=self.unit,
            source_container=loc,
            destination_container=loc,
        )

    def test_mixer_child_scaled_by_batch(self):
        run = run_explode(self.plan.id)
        potato = PlanRequirement.objects.get(
            run=run, product_id=self.potato.id,
        )
        expected = Decimal('4812000') * Decimal('96000') / Decimal('186080')
        self.assertEqual(potato.net_required, expected)
        self.assertLess(potato.net_required, Decimal('10000000'))
