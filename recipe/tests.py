from decimal import Decimal

from django.test import TestCase

from locations.models import Location
from product.models import Category, Product, ProductClass, Range, Unit
from recipe.models import Recipe, RecipeComponent, RecipeVersion, RecipeVersionStatus


class RecipeTreeApiTests(TestCase):
    def setUp(self):
        ProductClass.objects.create(id=1, name='Finished')
        Category.objects.create(id=1, name='Meals')
        Range.objects.create(id=1, name='Main')
        Unit.objects.create(id=1, name='Each')
        Location.objects.create(id=1, name='Spice Room', visible=True)
        Location.objects.create(id=2, name='Mixers', visible=True)
        Location.objects.create(id=3, name='Dispatch', visible=True)

    def _product(self, name, *, source_id=1, dest_id=2, recipe_code=None):
        return Product.objects.create(
            name=name,
            recipe_code=recipe_code,
            product_class_id=1,
            category_id=1,
            range_id=1,
            unit_id=1,
            source_container_id=source_id,
            destination_container_id=dest_id,
        )

    def test_tree_product_without_recipe(self):
        raw = self._product('Sugar', source_id=1, dest_id=1, recipe_code='SUGAR')
        resp = self.client.get(f'/recipe/product/{raw.id}/tree/')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()['data']
        self.assertEqual(data['root_product_id'], raw.id)
        self.assertEqual(len(data['nodes']), 1)
        self.assertEqual(data['edges'], [])
        self.assertEqual(data['tree']['product_id'], raw.id)
        self.assertFalse(data['tree']['has_recipe'])
        self.assertEqual(data['tree']['children'], [])
        self.assertEqual(data['tree']['from_location_name'], 'Spice Room')

    def test_tree_nested_component(self):
        child = self._product('Spice Mix', source_id=1, dest_id=2, recipe_code='SPICE')
        parent = self._product('Samosa FG', source_id=2, dest_id=3, recipe_code='FG')

        recipe = Recipe.objects.create(product=parent, name='Samosa')
        version = RecipeVersion.objects.create(
            recipe=recipe,
            version_number=1,
            status=RecipeVersionStatus.ACTIVE,
        )
        RecipeComponent.objects.create(
            recipe_version=version,
            line_no=1,
            component_product=child,
            quantity=Decimal('6.000000'),
            unit_id=1,
        )

        resp = self.client.get(f'/recipe/product/{parent.id}/tree/')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()['data']

        self.assertEqual(data['root_product_id'], parent.id)
        self.assertEqual({n['product_id'] for n in data['nodes']}, {parent.id, child.id})
        self.assertEqual(len(data['edges']), 1)
        self.assertEqual(data['edges'][0]['parent_product_id'], parent.id)
        self.assertEqual(data['edges'][0]['child_product_id'], child.id)
        self.assertEqual(data['edges'][0]['quantity'], '6.000000')
        self.assertEqual(data['edges'][0]['unit_name'], 'Each')

        self.assertTrue(data['tree']['has_recipe'])
        self.assertEqual(data['tree']['to_location_name'], 'Dispatch')
        self.assertEqual(len(data['tree']['children']), 1)
        self.assertEqual(data['tree']['children'][0]['product_id'], child.id)
        self.assertFalse(data['tree']['children'][0]['has_recipe'])

    def test_tree_product_not_found(self):
        resp = self.client.get('/recipe/product/999999/tree/')
        self.assertEqual(resp.status_code, 404)
