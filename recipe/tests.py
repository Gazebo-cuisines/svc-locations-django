from decimal import Decimal
import time
from unittest.mock import patch

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from django.test import TestCase

from locations.models import Location
from product.models import Category, Product, ProductClass, Range, Unit
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
            recipe=recipe, version_number=1, status=RecipeVersionStatus.DRAFT,
        )
        resp = self._post(f'/recipe/versions/{version.id}/activate/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['data']['status'], 'active')
