from decimal import Decimal
import time
from datetime import date
from io import BytesIO
from unittest.mock import MagicMock, patch

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase
from PIL import Image

from locations.models import Location
from product.models import Category, Product, ProductAudit, ProductClass, ProductImage, Range, Unit
from recipe.models import Recipe, RecipeComponent, RecipeVersion, RecipeVersionStatus
from users_rbac.models import AdminAccess, AdminArea, Department, RbacUser, UserDepartment

POOL = 'eu-west-2_testpool'
CLIENT_ID = 'client-id'
ISSUER = f'https://cognito-idp.eu-west-2.amazonaws.com/{POOL}'
ENV = {
    'COGNITO_USER_POOL_ID': POOL,
    'COGNITO_CLIENT_ID': CLIENT_ID,
    'COGNITO_REGION': 'eu-west-2',
}


class RecipeAuthMixin:
    def _recipe_auth(
        self, *, it=False, floor=False, sub='sub-recipe', username='recipe.user',
    ):
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        env = patch.dict('os.environ', ENV)
        env.start()
        self.addCleanup(env.stop)
        key_patch = patch(
            'users_rbac.auth._get_signing_key',
            return_value=private_key.public_key(),
        )
        key_patch.start()
        self.addCleanup(key_patch.stop)
        user = RbacUser.objects.create(
            cognito_sub=sub,
            username=username,
            display_name=username,
        )
        if it:
            UserDepartment.objects.create(user=user, department=Department.IT)
        elif floor:
            UserDepartment.objects.create(user=user, department=Department.PRODUCTION)
        else:
            UserDepartment.objects.create(user=user, department=Department.ADMIN)
            AdminAccess.objects.create(user=user, area=AdminArea.TECHNICAL)
        token = jwt.encode(
            {
                'sub': sub,
                'iss': ISSUER,
                'token_use': 'id',
                'aud': CLIENT_ID,
                'exp': int(time.time()) + 3600,
            },
            private_key,
            algorithm='RS256',
            headers={'kid': 'test-kid'},
        )
        self.auth = f'Bearer {token}'
        return user

    def _post(self, url, data='{}', auth=None):
        return self.client.post(
            url,
            data=data,
            content_type='application/json',
            HTTP_AUTHORIZATION=auth if auth is not None else self.auth,
        )


class RecipeTreeApiTests(TestCase):
    def setUp(self):
        ProductClass.objects.create(id=1, name='Finished')
        Category.objects.create(id=1, name='Meals')
        Range.objects.create(id=1, name='Main')
        Unit.objects.create(id=1, name='Each')
        Location.objects.create(id=1, name='Spice Room', visible=True)
        Location.objects.create(id=2, name='Mixers', visible=True)
        Location.objects.create(id=3, name='Dispatch', visible=True)

    def _product(self, name, *, source_id=1, dest_id=2, recipe_code=None, category_id=1):
        return Product.objects.create(
            name=name,
            recipe_code=recipe_code,
            product_class_id=1,
            category_id=category_id,
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
        self.assertIsNone(data['tree']['version_id'])
        self.assertIsNone(data['tree']['version_label'])
        self.assertEqual(data['tree']['versions'], [])
        self.assertEqual(data['tree']['children'], [])
        self.assertEqual(data['tree']['from_location_name'], 'Spice Room')
        self.assertEqual(data['tree']['category_id'], 1)
        self.assertEqual(data['tree']['category_name'], 'Meals')
        self.assertIsNone(data['tree']['category_image_url'])
        self.assertIsNone(data['tree']['image_url'])
        self.assertEqual(data['tree']['images'], [])

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

    def test_tree_version_picker_and_preview(self):
        child_a = self._product('Mix A', source_id=1, dest_id=2, recipe_code='MIXA')
        child_b = self._product('Mix B', source_id=1, dest_id=2, recipe_code='MIXB')
        parent = self._product('FG', source_id=2, dest_id=3, recipe_code='FG2')
        recipe = Recipe.objects.create(product=parent, name='FG')
        v1 = RecipeVersion.objects.create(
            recipe=recipe,
            version_number=1,
            status=RecipeVersionStatus.ACTIVE,
        )
        RecipeComponent.objects.create(
            recipe_version=v1,
            line_no=1,
            component_product=child_a,
            quantity=Decimal('1.000000'),
            unit_id=1,
        )
        v2 = RecipeVersion.objects.create(
            recipe=recipe,
            version_number=2,
            status=RecipeVersionStatus.APPROVED,
        )
        RecipeComponent.objects.create(
            recipe_version=v2,
            line_no=1,
            component_product=child_b,
            quantity=Decimal('1.000000'),
            unit_id=1,
        )

        live = self.client.get(f'/recipe/product/{parent.id}/tree/').json()['data']['tree']
        self.assertTrue(live['is_live'])
        self.assertEqual(live['version_status'], 'active')
        self.assertEqual(live['version_id'], v1.id)
        self.assertEqual(live['version_label'], 'v1')
        self.assertEqual(
            [
                (row['id'], row['version_label'], row['status'], row['can_activate'])
                for row in live['versions']
            ],
            [
                (v1.id, 'v1', 'active', False),
                (v2.id, 'v2', 'approved', True),
            ],
        )
        self.assertEqual(live['children'][0]['product_id'], child_a.id)
        self.assertIsNone(live['children'][0]['version_id'])
        self.assertEqual(live['children'][0]['versions'], [])

        preview = self.client.get(
            f'/recipe/product/{parent.id}/tree/?version_id={v2.id}',
        ).json()['data']['tree']
        self.assertEqual(preview['version_id'], v2.id)
        self.assertEqual(preview['version_label'], 'v2')
        self.assertFalse(preview['is_live'])
        self.assertEqual(preview['children'][0]['product_id'], child_b.id)

        bad = self.client.get(f'/recipe/product/{parent.id}/tree/?version_id=999999')
        self.assertEqual(bad.status_code, 400)

    def test_tree_nested_pins(self):
        chilli = self._product('Chilli', source_id=1, dest_id=1, recipe_code='CHILLI')
        salt = self._product('Salt', source_id=1, dest_id=1, recipe_code='SALT')
        sugar = self._product('Sugar', source_id=1, dest_id=1, recipe_code='SUGAR')
        spice = self._product('Samosa spice pack', source_id=1, dest_id=2, recipe_code='SPICE')
        parent = self._product('Samosa FG', source_id=2, dest_id=3, recipe_code='FG')

        spice_recipe = Recipe.objects.create(product=spice, name='Spice')
        v1 = RecipeVersion.objects.create(
            recipe=spice_recipe, version_number=1, status=RecipeVersionStatus.RETIRED,
        )
        RecipeComponent.objects.create(
            recipe_version=v1, line_no=1, component_product=chilli,
            quantity=Decimal('1.000000'), unit_id=1,
        )
        v2 = RecipeVersion.objects.create(
            recipe=spice_recipe, version_number=2, status=RecipeVersionStatus.RETIRED,
        )
        for i, product in enumerate((chilli, salt), start=1):
            RecipeComponent.objects.create(
                recipe_version=v2, line_no=i, component_product=product,
                quantity=Decimal('1.000000'), unit_id=1,
            )
        v3 = RecipeVersion.objects.create(
            recipe=spice_recipe, version_number=3, status=RecipeVersionStatus.ACTIVE,
        )
        for i, product in enumerate((chilli, salt, sugar), start=1):
            RecipeComponent.objects.create(
                recipe_version=v3, line_no=i, component_product=product,
                quantity=Decimal('1.000000'), unit_id=1,
            )

        parent_recipe = Recipe.objects.create(product=parent, name='FG')
        parent_v = RecipeVersion.objects.create(
            recipe=parent_recipe, version_number=1, status=RecipeVersionStatus.ACTIVE,
        )
        RecipeComponent.objects.create(
            recipe_version=parent_v, line_no=1, component_product=spice,
            quantity=Decimal('1.000000'), unit_id=1,
        )

        def spice_child_ids(tree):
            return [c['product_id'] for c in tree['children'][0]['children']]

        live = self.client.get(f'/recipe/product/{parent.id}/tree/').json()['data']
        spice_node = live['tree']['children'][0]
        self.assertEqual(spice_node['version_id'], v3.id)
        self.assertEqual(spice_node['version_label'], 'v3')
        self.assertEqual(spice_node['version_status'], 'active')
        self.assertEqual(
            [(row['id'], row['version_label'], row['status']) for row in spice_node['versions']],
            [
                (v1.id, 'v1', 'retired'),
                (v2.id, 'v2', 'retired'),
                (v3.id, 'v3', 'active'),
            ],
        )
        self.assertEqual(spice_child_ids(live['tree']), [chilli.id, salt.id, sugar.id])

        pinned = self.client.get(
            f'/recipe/product/{parent.id}/tree/?pins={spice.id}:{v2.id}',
        ).json()['data']
        spice_node = pinned['tree']['children'][0]
        self.assertEqual(spice_node['version_id'], v2.id)
        self.assertEqual(spice_node['version_label'], 'v2')
        self.assertEqual(spice_node['version_status'], 'retired')
        self.assertEqual(spice_child_ids(pinned['tree']), [chilli.id, salt.id])
        self.assertEqual(
            {
                e['child_product_id']
                for e in pinned['edges']
                if e['parent_product_id'] == spice.id
            },
            {chilli.id, salt.id},
        )
        self.assertNotIn(sugar.id, {n['product_id'] for n in pinned['nodes']})

        v1_tree = self.client.get(
            f'/recipe/product/{parent.id}/tree/?pins={spice.id}:{v1.id}',
        ).json()['data']['tree']
        self.assertEqual(spice_child_ids(v1_tree), [chilli.id])

        combo = self.client.get(
            f'/recipe/product/{parent.id}/tree/'
            f'?version_id={parent_v.id}&pins={spice.id}:{v2.id}',
        ).json()['data']['tree']
        self.assertEqual(combo['version_id'], parent_v.id)
        self.assertEqual(spice_child_ids(combo), [chilli.id, salt.id])

        self.assertEqual(
            self.client.get(f'/recipe/product/{parent.id}/tree/?pins=nope').status_code,
            400,
        )
        self.assertEqual(
            self.client.get(
                f'/recipe/product/{parent.id}/tree/?pins={spice.id}:999999',
            ).status_code,
            400,
        )
        self.assertEqual(
            self.client.get(
                f'/recipe/product/{parent.id}/tree/?pins={spice.id}:{parent_v.id}',
            ).status_code,
            400,
        )

    def test_tree_product_not_found(self):
        resp = self.client.get('/recipe/product/999999/tree/')
        self.assertEqual(resp.status_code, 404)

    @patch('recipe.utils.category_image_url', side_effect=lambda cat: f'https://img/{cat.image_key}')
    def test_tree_includes_own_category_image(self, _mock):
        packed = Category.objects.create(
            id=2, name='Packed Items', image_key='Product-category/tray.jpg',
        )
        cased = Category.objects.create(
            id=3, name='Cased Items', image_key='Product-category/box.jpg',
        )
        Location.objects.create(id=4, name='High Risk', visible=True)
        Location.objects.create(id=5, name='Sleeving', visible=True)
        tray = self._product(
            'Tray pack', source_id=4, dest_id=5, recipe_code='TRAY',
            category_id=packed.id,
        )
        case = self._product(
            'Outer case', source_id=5, dest_id=3, recipe_code='CASE',
            category_id=cased.id,
        )
        recipe = Recipe.objects.create(product=case, name='Case')
        version = RecipeVersion.objects.create(
            recipe=recipe,
            version_number=1,
            status=RecipeVersionStatus.ACTIVE,
        )
        RecipeComponent.objects.create(
            recipe_version=version,
            line_no=1,
            component_product=tray,
            quantity=Decimal('6.000000'),
            unit_id=1,
        )

        resp = self.client.get(f'/recipe/product/{case.id}/tree/')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()['data']
        by_id = {n['product_id']: n for n in data['nodes']}
        self.assertEqual(by_id[case.id]['category_name'], 'Cased Items')
        self.assertEqual(
            by_id[case.id]['category_image_url'], 'https://img/Product-category/box.jpg',
        )
        self.assertEqual(by_id[tray.id]['category_name'], 'Packed Items')
        self.assertEqual(
            by_id[tray.id]['category_image_url'], 'https://img/Product-category/tray.jpg',
        )
        self.assertEqual(data['tree']['category_image_url'], by_id[case.id]['category_image_url'])
        self.assertEqual(
            data['tree']['children'][0]['category_image_url'],
            by_id[tray.id]['category_image_url'],
        )
        self.assertEqual(by_id[case.id]['image_url'], by_id[case.id]['category_image_url'])
        self.assertEqual(by_id[tray.id]['image_url'], by_id[tray.id]['category_image_url'])

    @patch('recipe.utils.category_image_url', side_effect=lambda cat: f'https://img/{cat.image_key}')
    def test_tree_raw_material_uses_category_image(self, _mock):
        Category.objects.filter(pk=1).update(image_key='Product-category/potato.jpg')
        raw = self._product('POTATO DICED', source_id=1, dest_id=1, recipe_code='RM-POT')
        resp = self.client.get(f'/recipe/product/{raw.id}/tree/')
        self.assertEqual(resp.status_code, 200)
        node = resp.json()['data']['tree']
        self.assertFalse(node['has_recipe'])
        self.assertEqual(node['image_url'], 'https://img/Product-category/potato.jpg')
        self.assertEqual(node['image_url'], node['category_image_url'])

    @patch('recipe.utils.category_image_url', side_effect=lambda cat: f'https://img/{cat.image_key}')
    @patch('product.product_images.product_image_url', side_effect=lambda row, **_: f'https://img/{row.image_key}')
    def test_tree_product_main_image_beats_category(self, _product_img, _cat):
        Category.objects.filter(pk=1).update(image_key='Product-category/potato.jpg')
        raw = self._product('POTATO DICED', source_id=1, dest_id=1, recipe_code='RM-POT')
        extra = ProductImage.objects.create(
            product=raw, image_key='Product/side.jpg', is_main=False, sort_order=1,
        )
        main = ProductImage.objects.create(
            product=raw, image_key='Product/main.jpg', is_main=True, sort_order=0,
        )
        resp = self.client.get(f'/recipe/product/{raw.id}/tree/')
        self.assertEqual(resp.status_code, 200)
        node = resp.json()['data']['tree']
        self.assertEqual(node['image_url'], 'https://img/Product/main.jpg')
        self.assertEqual(node['category_image_url'], 'https://img/Product-category/potato.jpg')
        self.assertEqual(len(node['images']), 2)
        self.assertEqual(node['images'][0]['id'], main.id)
        self.assertTrue(node['images'][0]['is_main'])
        self.assertEqual(node['images'][1]['id'], extra.id)


class RecipeVersionNumberTests(RecipeAuthMixin, TestCase):
    def setUp(self):
        self._recipe_auth()
        ProductClass.objects.create(id=1, name='Finished')
        Category.objects.create(id=1, name='Meals')
        Range.objects.create(id=1, name='Main')
        Unit.objects.create(id=1, name='Each')
        Location.objects.create(id=1, name='Spice Room', visible=True)
        Location.objects.create(id=2, name='Mixers', visible=True)

    def _product(self, name):
        return Product.objects.create(
            name=name,
            product_class_id=1,
            category_id=1,
            range_id=1,
            unit_id=1,
            source_container_id=1,
            destination_container_id=2,
        )

    def test_two_products_each_start_at_version_one(self):
        a = Recipe.objects.create(product=self._product('FG A'), name='A')
        b = Recipe.objects.create(product=self._product('FG B'), name='B')
        r1 = self._post(f'/recipe/{a.id}/versions/')
        r2 = self._post(f'/recipe/{b.id}/versions/')
        self.assertEqual(r1.status_code, 201)
        self.assertEqual(r2.status_code, 201)
        self.assertEqual(r1.json()['data']['version_number'], 1)
        self.assertEqual(r2.json()['data']['version_number'], 1)
        self.assertEqual(r1.json()['data']['version_label'], 'v1')
        self.assertEqual(r1.json()['data']['component_count'], 0)

    def test_second_version_is_two(self):
        recipe = Recipe.objects.create(product=self._product('FG'), name='FG')
        RecipeVersion.objects.create(
            recipe=recipe, version_number=1, status=RecipeVersionStatus.DRAFT,
        )
        resp = self._post(f'/recipe/{recipe.id}/versions/')
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.json()['data']['version_number'], 2)
        self.assertEqual(resp.json()['data']['version_label'], 'v2')

    def test_client_version_number_ignored(self):
        recipe = Recipe.objects.create(product=self._product('FG'), name='FG')
        resp = self._post(
            f'/recipe/{recipe.id}/versions/',
            data='{"version_number": 99}',
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.json()['data']['version_number'], 1)

    def test_component_payload_includes_version_number(self):
        parent = self._product('FG')
        child = self._product('Spice')
        child.recipe_code = 'SUG-01'
        child.save(update_fields=['recipe_code'])
        recipe = Recipe.objects.create(product=parent, name='FG')
        version = RecipeVersion.objects.create(
            recipe=recipe, version_number=1, status=RecipeVersionStatus.DRAFT,
        )
        resp = self._post(
            f'/recipe/versions/{version.id}/components/',
            data=(
                '{"line_no": 1, "component_product_id": %s,'
                ' "quantity": "6", "unit_id": 1}'
            ) % child.id,
        )
        self.assertEqual(resp.status_code, 201)
        data = resp.json()['data']
        self.assertEqual(data['version_number'], 1)
        self.assertEqual(data['recipe_id'], recipe.id)
        self.assertEqual(data['recipe_version_id'], version.id)
        self.assertEqual(data['component_recipe_code'], 'SUG-01')

    def test_copy_from_version_clones_header_and_lines(self):
        parent = self._product('FG')
        child = self._product('Spice')
        recipe = Recipe.objects.create(product=parent, name='FG')
        source = RecipeVersion.objects.create(
            recipe=recipe,
            version_number=1,
            status=RecipeVersionStatus.ACTIVE,
            process_loss=Decimal('1.0500'),
            batch_quantity=Decimal('100.000000'),
            remarks='v1',
        )
        RecipeComponent.objects.create(
            recipe_version=source,
            line_no=1,
            component_product=child,
            quantity=Decimal('6.000000'),
            unit_id=1,
            step_instructions='fold',
        )
        resp = self._post(
            f'/recipe/{recipe.id}/versions/',
            data='{"copy_from_version_id": %s}' % source.id,
        )
        self.assertEqual(resp.status_code, 201)
        data = resp.json()['data']
        self.assertEqual(data['version_number'], 2)
        self.assertEqual(data['status'], 'draft')
        self.assertEqual(data['process_loss'], '1.0500')
        self.assertEqual(data['batch_quantity'], '100.000000')
        self.assertEqual(data['remarks'], 'v1')
        self.assertEqual(data['copied_from_version_id'], source.id)
        self.assertEqual(len(data['components']), 1)
        self.assertEqual(data['components'][0]['component_product_id'], child.id)
        self.assertEqual(data['components'][0]['quantity'], '6.000000')
        self.assertEqual(data['components'][0]['step_instructions'], 'fold')

    def test_copy_from_other_recipe_is_400(self):
        a = Recipe.objects.create(product=self._product('FG A'), name='A')
        b = Recipe.objects.create(product=self._product('FG B'), name='B')
        other = RecipeVersion.objects.create(
            recipe=b, version_number=1, status=RecipeVersionStatus.DRAFT,
        )
        resp = self._post(
            f'/recipe/{a.id}/versions/',
            data='{"copy_from_version_id": %s}' % other.id,
        )
        self.assertEqual(resp.status_code, 400)

    def test_copy_from_missing_is_404(self):
        recipe = Recipe.objects.create(product=self._product('FG'), name='FG')
        resp = self._post(
            f'/recipe/{recipe.id}/versions/',
            data='{"copy_from_version_id": 999999}',
        )
        self.assertEqual(resp.status_code, 404)


class RecipeAutoProvisionTests(RecipeAuthMixin, TestCase):
    def setUp(self):
        self._recipe_auth()
        ProductClass.objects.create(id=1, name='Finished')
        Category.objects.create(id=1, name='Meals')
        Range.objects.create(id=1, name='Main')
        Unit.objects.create(id=1, name='Each')
        Location.objects.create(id=1, name='Spice Room', visible=True)
        Location.objects.create(id=2, name='Mixers', visible=True)

    def _product(self, name):
        return Product.objects.create(
            name=name,
            product_class_id=1,
            category_id=1,
            range_id=1,
            unit_id=1,
            source_container_id=1,
            destination_container_id=2,
        )

    def test_by_product_creates_draft_v1(self):
        product = self._product('Samosa FG')
        resp = self.client.get(f'/recipe/product/{product.id}/')
        self.assertEqual(resp.status_code, 201)
        data = resp.json()['data']
        self.assertEqual(data['product_id'], product.id)
        self.assertEqual(len(data['versions']), 1)
        self.assertEqual(data['versions'][0]['version_number'], 1)
        self.assertEqual(data['versions'][0]['status'], 'draft')
        self.assertEqual(data['versions'][0]['component_count'], 0)

    def test_by_product_second_call_is_idempotent(self):
        product = self._product('Samosa FG')
        first = self.client.get(f'/recipe/product/{product.id}/')
        second = self.client.get(f'/recipe/product/{product.id}/')
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json()['data']['id'], second.json()['data']['id'])
        self.assertEqual(len(second.json()['data']['versions']), 1)

    def test_by_product_missing_is_404(self):
        resp = self.client.get('/recipe/product/999999/')
        self.assertEqual(resp.status_code, 404)

    def test_post_recipe_is_idempotent(self):
        product = self._product('Samosa FG')
        body = '{"product_id": %s}' % product.id
        first = self._post('/recipe/', data=body)
        second = self._post('/recipe/', data=body)
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(len(first.json()['data']['versions']), 1)
        self.assertEqual(first.json()['data']['id'], second.json()['data']['id'])


class RecipeListApiTests(TestCase):
    def setUp(self):
        ProductClass.objects.create(id=1, name='Finished')
        food = Category.objects.create(id=10, name='Food', path='Food')
        frozen = Category.objects.create(
            id=11, name='Frozen', path='Food > Frozen', parent=food,
        )
        Category.objects.create(
            id=1, name='Meals', path='Food > Frozen > Meals', parent=frozen,
        )
        Range.objects.create(id=1, name='Main')
        Unit.objects.create(id=1, name='Each')
        Unit.objects.create(id=2, name='g')
        Location.objects.create(id=1, name='Spice Room', visible=True)
        Location.objects.create(id=2, name='Mixers', visible=True)

    def _product(self, name):
        return Product.objects.create(
            name=name,
            recipe_code=name.replace(' ', '-')[:32],
            product_class_id=1,
            category_id=1,
            range_id=1,
            unit_id=1,
            source_container_id=1,
            destination_container_id=2,
        )

    def test_list_is_light_and_includes_category(self):
        parent = self._product('Samosa FG')
        spice = self._product('Spice Mix')
        pastry = self._product('Pastry')
        recipe = Recipe.objects.create(product=parent, name='Samosa')
        draft = RecipeVersion.objects.create(
            recipe=recipe,
            version_number=2,
            status=RecipeVersionStatus.DRAFT,
        )
        active = RecipeVersion.objects.create(
            recipe=recipe,
            version_number=1,
            status=RecipeVersionStatus.ACTIVE,
            batch_quantity=Decimal('210760'),
            batch_unit_id=2,
        )
        RecipeComponent.objects.create(
            recipe_version=active,
            line_no=1,
            component_product=spice,
            quantity=Decimal('6.000000'),
            unit_id=1,
        )
        RecipeComponent.objects.create(
            recipe_version=draft,
            line_no=1,
            component_product=spice,
            quantity=Decimal('1.000000'),
            unit_id=1,
        )
        RecipeComponent.objects.create(
            recipe_version=draft,
            line_no=2,
            component_product=pastry,
            quantity=Decimal('2.000000'),
            unit_id=1,
        )

        resp = self.client.get('/recipe/')
        self.assertEqual(resp.status_code, 200)
        rows = resp.json()['data']
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertNotIn('ingredients', row)
        self.assertEqual(row['name'], 'Samosa')
        self.assertEqual(row['recipe_code'], 'Samosa-FG')
        self.assertEqual(row['ingredient_count'], 1)
        self.assertEqual(row['category_id'], 1)
        self.assertEqual(row['category_name'], 'Meals')
        self.assertEqual(row['categories'], [
            {'id': 10, 'name': 'Food'},
            {'id': 11, 'name': 'Frozen'},
            {'id': 1, 'name': 'Meals'},
        ])
        self.assertEqual(row['version_number'], 1)
        self.assertEqual(row['status'], 'active')
        self.assertEqual(row['batch_quantity'], '210760.000000')
        self.assertEqual(row['batch_unit_id'], 2)
        self.assertEqual(row['batch_unit_name'], 'g')

        detail = self.client.get(f'/recipe/{recipe.id}/')
        self.assertEqual(detail.status_code, 200)
        data = detail.json()['data']
        self.assertEqual(len(data['ingredients']), 1)
        self.assertEqual(data['ingredient_count'], 1)
        self.assertEqual(data['category_id'], 1)


class RecipeGateTests(RecipeAuthMixin, TestCase):
    def setUp(self):
        ProductClass.objects.create(id=1, name='Finished')
        Category.objects.create(id=1, name='Meals')
        Range.objects.create(id=1, name='Main')
        Unit.objects.create(id=1, name='Each')
        Location.objects.create(id=1, name='Spice Room', visible=True)
        Location.objects.create(id=2, name='Mixers', visible=True)

    def _product(self, name):
        return Product.objects.create(
            name=name,
            product_class_id=1,
            category_id=1,
            range_id=1,
            unit_id=1,
            source_container_id=1,
            destination_container_id=2,
        )

    def test_write_without_token_is_401(self):
        recipe = Recipe.objects.create(product=self._product('FG'), name='FG')
        resp = self.client.post(
            f'/recipe/{recipe.id}/versions/',
            data='{}',
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 401)

    def test_activate_as_floor_is_403(self):
        self._recipe_auth(floor=True, sub='sub-floor', username='floor.user')
        recipe = Recipe.objects.create(product=self._product('FG'), name='FG')
        version = RecipeVersion.objects.create(
            recipe=recipe, version_number=1, status=RecipeVersionStatus.DRAFT,
        )
        resp = self._post(f'/recipe/versions/{version.id}/activate/')
        self.assertEqual(resp.status_code, 403)

    def test_activate_as_it_succeeds(self):
        self._recipe_auth(it=True, sub='sub-it', username='it.user')
        recipe = Recipe.objects.create(product=self._product('FG'), name='FG')
        version = RecipeVersion.objects.create(
            recipe=recipe, version_number=1, status=RecipeVersionStatus.APPROVED,
        )
        resp = self._post(f'/recipe/versions/{version.id}/activate/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['data']['status'], 'active')


class RecipeAuditTests(RecipeAuthMixin, TestCase):
    def setUp(self):
        self._recipe_auth()
        ProductClass.objects.create(id=1, name='Finished')
        Category.objects.create(id=1, name='Meals')
        Range.objects.create(id=1, name='Main')
        Unit.objects.create(id=1, name='Each')
        Location.objects.create(id=1, name='Spice Room', visible=True)
        Location.objects.create(id=2, name='Mixers', visible=True)

    def _product(self, name):
        return Product.objects.create(
            name=name,
            product_class_id=1,
            category_id=1,
            range_id=1,
            unit_id=1,
            source_container_id=1,
            destination_container_id=2,
        )

    def test_component_quantity_writes_before_after(self):
        parent = self._product('FG')
        child = self._product('Spice')
        recipe = Recipe.objects.create(product=parent, name='FG')
        version = RecipeVersion.objects.create(
            recipe=recipe, version_number=1, status=RecipeVersionStatus.DRAFT,
        )
        created = self._post(
            f'/recipe/versions/{version.id}/components/',
            data=(
                '{"line_no": 1, "component_product_id": %s,'
                ' "quantity": "6", "unit_id": 1}'
            ) % child.id,
        )
        self.assertEqual(created.status_code, 201)
        comp_id = created.json()['data']['id']
        patched = self.client.patch(
            f'/recipe/components/{comp_id}/',
            data='{"quantity": "8"}',
            content_type='application/json',
            HTTP_AUTHORIZATION=self.auth,
        )
        self.assertEqual(patched.status_code, 200)
        events = self.client.get(f'/recipe/{recipe.id}/audit/').json()['data']
        entities = {e['entity'] for e in events}
        self.assertEqual(entities, {'recipe_component'})
        qty = [e for e in events if e['action'] == 'update'][0]
        self.assertEqual(qty['actor_name'], 'recipe.user')
        self.assertIn('quantity', qty['changed_fields'])
        self.assertEqual(qty['before_json']['quantity'], '6.000000')
        self.assertEqual(qty['after_json']['quantity'], '8.000000')

    def test_audit_hides_unrelated_product_events(self):
        parent = self._product('FG')
        recipe = Recipe.objects.create(product=parent, name='FG')
        ProductAudit.objects.create(
            product_id=parent.id,
            timeline_events=[{
                'entity': 'flags',
                'action': 'update',
                'before_json': {'has_recipe': False},
                'after_json': {'has_recipe': True},
            }],
        )
        self._post(f'/recipe/{recipe.id}/versions/')
        events = self.client.get(f'/recipe/{recipe.id}/audit/').json()['data']
        self.assertTrue(events)
        self.assertTrue(all(e['entity'] != 'flags' for e in events))


class RecipeApprovalTests(RecipeAuthMixin, TestCase):
    def setUp(self):
        self._recipe_auth()
        ProductClass.objects.create(id=1, name='Finished')
        Category.objects.create(id=1, name='Meals')
        Range.objects.create(id=1, name='Main')
        Unit.objects.create(id=1, name='Each')
        Location.objects.create(id=1, name='Spice Room', visible=True)
        Location.objects.create(id=2, name='Mixers', visible=True)

    def _product(self, name):
        return Product.objects.create(
            name=name,
            product_class_id=1,
            category_id=1,
            range_id=1,
            unit_id=1,
            source_container_id=1,
            destination_container_id=2,
        )

    def _draft_with_line(self):
        parent = self._product('FG')
        child = self._product('Spice')
        recipe = Recipe.objects.create(product=parent, name='FG')
        version = RecipeVersion.objects.create(
            recipe=recipe, version_number=1, status=RecipeVersionStatus.DRAFT,
        )
        RecipeComponent.objects.create(
            recipe_version=version,
            line_no=1,
            component_product=child,
            quantity=Decimal('6'),
            unit_id=1,
        )
        return recipe, version, child

    def test_submit_without_components_is_400(self):
        recipe = Recipe.objects.create(product=self._product('FG'), name='FG')
        version = RecipeVersion.objects.create(
            recipe=recipe, version_number=1, status=RecipeVersionStatus.DRAFT,
        )
        resp = self._post(f'/recipe/versions/{version.id}/submit/')
        self.assertEqual(resp.status_code, 400)

    def test_approve_without_reason_is_400(self):
        _, version, _ = self._draft_with_line()
        self._post(f'/recipe/versions/{version.id}/submit/')
        resp = self._post(
            f'/recipe/versions/{version.id}/approve/',
            data='{"effective_from": "2020-01-01"}',
        )
        self.assertEqual(resp.status_code, 400)

    def test_approve_as_floor_is_403(self):
        _, version, _ = self._draft_with_line()
        self._post(f'/recipe/versions/{version.id}/submit/')
        self._recipe_auth(floor=True, sub='sub-floor2', username='floor2.user')
        resp = self._post(
            f'/recipe/versions/{version.id}/approve/',
            data='{"reason": "ok", "effective_from": "2020-01-01"}',
        )
        self.assertEqual(resp.status_code, 403)

    def test_approve_past_date_activates(self):
        _, version, _ = self._draft_with_line()
        self._post(f'/recipe/versions/{version.id}/submit/')
        resp = self._post(
            f'/recipe/versions/{version.id}/approve/',
            data='{"reason": "go live", "effective_from": "2020-01-01"}',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['data']['status'], 'active')
        self.assertEqual(resp.json()['data']['approval_reason'], 'go live')

    def test_approve_future_stays_approved(self):
        _, version, _ = self._draft_with_line()
        self._post(f'/recipe/versions/{version.id}/submit/')
        resp = self._post(
            f'/recipe/versions/{version.id}/approve/',
            data='{"reason": "later", "effective_from": "2099-01-01"}',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['data']['status'], 'approved')

    def test_component_on_pending_is_409(self):
        recipe, version, child = self._draft_with_line()
        self._post(f'/recipe/versions/{version.id}/submit/')
        resp = self._post(
            f'/recipe/versions/{version.id}/components/',
            data=(
                '{"line_no": 2, "component_product_id": %s,'
                ' "quantity": "1", "unit_id": 1}'
            ) % child.id,
        )
        self.assertEqual(resp.status_code, 409)


class RecipeScheduledActivationTests(TestCase):
    def setUp(self):
        ProductClass.objects.create(id=1, name='Finished')
        Category.objects.create(id=1, name='Meals')
        Range.objects.create(id=1, name='Main')
        Unit.objects.create(id=1, name='Each')
        Location.objects.create(id=1, name='Spice Room', visible=True)
        Location.objects.create(id=2, name='Mixers', visible=True)
        Product.objects.create(
            name='Due FG',
            product_class_id=1,
            category_id=1,
            range_id=1,
            unit_id=1,
            source_container_id=1,
            destination_container_id=2,
        )
        self.product = Product.objects.get(name='Due FG')
        self.recipe = Recipe.objects.create(product=self.product, name='Due FG')

    def test_dry_run_does_not_activate(self):
        version = RecipeVersion.objects.create(
            recipe=self.recipe,
            version_number=1,
            status=RecipeVersionStatus.APPROVED,
            effective_from=date(2020, 1, 1),
        )
        call_command('activate_due_recipe_versions', dry_run=True)
        version.refresh_from_db()
        self.assertEqual(version.status, RecipeVersionStatus.APPROVED)

    def test_due_version_activates(self):
        version = RecipeVersion.objects.create(
            recipe=self.recipe,
            version_number=1,
            status=RecipeVersionStatus.APPROVED,
            effective_from=date(2020, 1, 1),
        )
        RecipeVersion.objects.create(
            recipe=self.recipe,
            version_number=2,
            status=RecipeVersionStatus.APPROVED,
            effective_from=date(2099, 1, 1),
        )
        call_command('activate_due_recipe_versions')
        version.refresh_from_db()
        self.assertEqual(version.status, RecipeVersionStatus.ACTIVE)
        future = self.recipe.versions.get(version_number=2)
        self.assertEqual(future.status, RecipeVersionStatus.APPROVED)


class RecipeAttachmentTests(RecipeAuthMixin, TestCase):
    def setUp(self):
        ProductClass.objects.create(id=1, name='Finished')
        Category.objects.create(id=1, name='Meals')
        Range.objects.create(id=1, name='Main')
        Unit.objects.create(id=1, name='Each')
        Location.objects.create(id=1, name='Spice Room', visible=True)
        Location.objects.create(id=2, name='Mixers', visible=True)
        self.parent = Product.objects.create(
            name='Att FG',
            product_class_id=1,
            category_id=1,
            range_id=1,
            unit_id=1,
            source_container_id=1,
            destination_container_id=2,
        )
        self.child = Product.objects.create(
            name='Att Spice',
            product_class_id=1,
            category_id=1,
            range_id=1,
            unit_id=1,
            source_container_id=1,
            destination_container_id=2,
        )
        self.recipe = Recipe.objects.create(product=self.parent, name='Att FG')
        self.version = RecipeVersion.objects.create(
            recipe=self.recipe, version_number=1, status=RecipeVersionStatus.DRAFT,
        )
        self.component = RecipeComponent.objects.create(
            recipe_version=self.version,
            line_no=1,
            component_product=self.child,
            quantity=Decimal('1'),
            unit_id=1,
        )
        self._recipe_auth()

    def _jpeg(self):
        buf = BytesIO()
        Image.new('RGB', (8, 8), (200, 40, 40)).save(buf, format='JPEG')
        return SimpleUploadedFile(
            'hero.jpg', buf.getvalue(), content_type='image/jpeg',
        )

    def _s3(self, s3_factory):
        s3 = MagicMock()
        s3.generate_presigned_url.return_value = 'https://s3.example/signed'
        s3_factory.return_value = s3
        return s3

    @patch('recipe.attachments._s3_client')
    def test_upload_hero_on_draft(self, s3_factory):
        self._s3(s3_factory)
        resp = self.client.post(
            f'/recipe/versions/{self.version.id}/attachments/',
            {'file': self._jpeg(), 'kind': 'hero', 'caption': 'finished'},
            HTTP_AUTHORIZATION=self.auth,
        )
        self.assertEqual(resp.status_code, 201)
        data = resp.json()['data']
        self.assertEqual(data['kind'], 'hero')
        self.assertIsNone(data['component_id'])
        self.assertEqual(data['url'], 'https://s3.example/signed')
        self.assertTrue(data['original_filename'].endswith('.jpg'))
        detail = self.client.get(f'/recipe/versions/{self.version.id}/')
        self.assertEqual(len(detail.json()['data']['attachments']), 1)

    @patch('recipe.attachments._s3_client')
    def test_step_photo_on_component(self, s3_factory):
        self._s3(s3_factory)
        resp = self.client.post(
            f'/recipe/versions/{self.version.id}/attachments/',
            {
                'file': self._jpeg(),
                'kind': 'step',
                'component_id': str(self.component.id),
            },
            HTTP_AUTHORIZATION=self.auth,
        )
        self.assertEqual(resp.status_code, 201)
        detail = self.client.get(f'/recipe/versions/{self.version.id}/')
        data = detail.json()['data']
        self.assertEqual(data['attachments'], [])
        self.assertEqual(len(data['components'][0]['attachments']), 1)
        self.assertEqual(
            data['components'][0]['attachments'][0]['component_id'],
            self.component.id,
        )

    @patch('recipe.attachments._s3_client')
    def test_upload_on_pending_is_409(self, s3_factory):
        self._s3(s3_factory)
        self.version.status = RecipeVersionStatus.PENDING_APPROVAL
        self.version.save(update_fields=['status'])
        resp = self.client.post(
            f'/recipe/versions/{self.version.id}/attachments/',
            {'file': self._jpeg(), 'kind': 'hero'},
            HTTP_AUTHORIZATION=self.auth,
        )
        self.assertEqual(resp.status_code, 409)

    @patch('recipe.attachments._s3_client')
    def test_pdf_upload(self, s3_factory):
        self._s3(s3_factory)
        resp = self.client.post(
            f'/recipe/versions/{self.version.id}/attachments/',
            {
                'file': SimpleUploadedFile(
                    'doc.pdf', b'%PDF-1.4', content_type='application/pdf',
                ),
            },
            HTTP_AUTHORIZATION=self.auth,
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.json()['data']['content_type'], 'application/pdf')

    def test_exe_is_400(self):
        resp = self.client.post(
            f'/recipe/versions/{self.version.id}/attachments/',
            {
                'file': SimpleUploadedFile(
                    'app.exe', b'MZ', content_type='application/x-msdownload',
                ),
            },
            HTTP_AUTHORIZATION=self.auth,
        )
        self.assertEqual(resp.status_code, 400)

    @patch('recipe.attachments._s3_client')
    def test_delete_attachment(self, s3_factory):
        self._s3(s3_factory)
        created = self.client.post(
            f'/recipe/versions/{self.version.id}/attachments/',
            {'file': self._jpeg(), 'kind': 'hero'},
            HTTP_AUTHORIZATION=self.auth,
        ).json()['data']
        resp = self.client.delete(
            f"/recipe/attachments/{created['id']}/",
            HTTP_AUTHORIZATION=self.auth,
        )
        self.assertEqual(resp.status_code, 200)
        listed = self.client.get(
            f'/recipe/versions/{self.version.id}/attachments/',
        )
        self.assertEqual(listed.json()['data']['count'], 0)
