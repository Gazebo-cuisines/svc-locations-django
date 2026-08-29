"""Product list filter by category subtree (Raw Materials, etc.)."""

from django.test import Client, TestCase

from locations.models import Location
from product.models import Category, Product, ProductClass, Unit
from product.query import category_subtree_ids


class CategorySubtreeProductSearchTests(TestCase):
    def setUp(self):
        ProductClass.objects.create(id=1, name='Class')
        Unit.objects.create(id=1, name='Kg')
        self.wh = Location.objects.create(id=1, name='WH', visible=True)

        self.raw_root = Category.objects.create(
            id=73, name='Raw Materials', path_nodes='(73)',
        )
        self.spice = Category.objects.create(
            id=6, name='SPICE', parent=self.raw_root, path_nodes='(73,6)',
        )
        self.blended = Category.objects.create(
            id=7, name='BLENDED', parent=self.spice, path_nodes='(73,6,7)',
        )
        self.pack_root = Category.objects.create(
            id=80, name='Packaging Materials', path_nodes='(80)',
        )
        self.film = Category.objects.create(
            id=81, name='FILM', parent=self.pack_root, path_nodes='(80,81)',
        )

        self.turmeric = Product.objects.create(
            name='Turmeric ground',
            recipe_code='TURM1',
            product_class_id=1,
            category=self.blended,
            unit_id=1,
            source_container=self.wh,
            destination_container=self.wh,
        )
        self.salt = Product.objects.create(
            name='Salt',
            recipe_code='SALT1',
            product_class_id=1,
            category=self.spice,
            unit_id=1,
            source_container=self.wh,
            destination_container=self.wh,
        )
        self.sleeve = Product.objects.create(
            name='Sleeve film',
            recipe_code='FILM1',
            product_class_id=1,
            category=self.film,
            unit_id=1,
            source_container=self.wh,
            destination_container=self.wh,
        )

    def test_subtree_ids_raw_materials(self):
        ids = set(category_subtree_ids(73))
        self.assertEqual(ids, {73, 6, 7})

    def test_subtree_ids_non_root_branch(self):
        """DAIRY-style path '(73,6)' must include self + children, not look for '(6)'."""
        ids = set(category_subtree_ids(6))
        self.assertEqual(ids, {6, 7})

    def test_list_filter_category_id_includes_descendants(self):
        client = Client()
        resp = client.get('/product/', {'category_id': '73', 'q': 'turm'})
        self.assertEqual(resp.status_code, 200)
        names = {row['name'] for row in resp.json()['data']}
        self.assertIn('Turmeric ground', names)
        self.assertNotIn('Sleeve film', names)

    def test_list_filter_excludes_other_trees(self):
        client = Client()
        resp = client.get('/product/', {'category_id': '73'})
        self.assertEqual(resp.status_code, 200)
        names = {row['name'] for row in resp.json()['data']}
        self.assertEqual(names, {'Turmeric ground', 'Salt'})

    def test_category_not_found(self):
        client = Client()
        resp = client.get('/product/', {'category_id': '99999'})
        self.assertEqual(resp.status_code, 404)
