from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import connection
from django.test import RequestFactory, SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext

from locations.models import (
    Location,
    LocationRelationType,
    LocationRole,
    LocationRoleAssignment,
)
from locations.services.hierarchy import assert_valid_edge
from locations.services.location_tree import build_location_tree
from locations.views.location_views import location_list_api


class HierarchyValidationTests(SimpleTestCase):
    def test_self_edge_rejected(self):
        with self.assertRaises(ValidationError):
            assert_valid_edge(3, 3, LocationRelationType.ZONE_GROUP)


def _location(location_id: int, name: str) -> Location:
    return Location(
        id=location_id,
        name=name,
        visible=True,
        static=False,
        locked=False,
    )


class LocationTreeBuildTests(SimpleTestCase):
    def _list_dict(self, location: Location) -> dict:
        return {
            'id': location.id,
            'name': location.name,
            'roles': [],
            'features': [],
        }

    @patch('locations.services.location_tree.location_list_dict')
    def test_parent_with_two_children(self, mock_list_dict):
        mock_list_dict.side_effect = self._list_dict
        parent = _location(1, 'Zone Parent')
        child_a = _location(2, 'Child A')
        child_b = _location(3, 'Child B')
        locations_by_id = {
            parent.id: parent,
            child_a.id: child_a,
            child_b.id: child_b,
        }
        edges = [(parent.id, child_a.id), (parent.id, child_b.id)]
        tree = build_location_tree(locations_by_id, zone_edges=edges)
        self.assertEqual(len(tree), 1)
        self.assertEqual(tree[0]['id'], parent.id)
        self.assertEqual(len(tree[0]['children']), 2)
        child_ids = {node['id'] for node in tree[0]['children']}
        self.assertEqual(child_ids, {child_a.id, child_b.id})

    @patch('locations.services.location_tree.location_list_dict')
    def test_orphan_is_root_with_empty_children(self, mock_list_dict):
        mock_list_dict.side_effect = self._list_dict
        orphan = _location(4, 'Orphan')
        tree = build_location_tree({orphan.id: orphan}, zone_edges=[])
        self.assertEqual(len(tree), 1)
        self.assertEqual(tree[0]['children'], [])

    @patch('locations.services.location_tree.location_list_dict')
    def test_deep_chain(self, mock_list_dict):
        mock_list_dict.side_effect = self._list_dict
        root = _location(10, 'Root')
        mid = _location(11, 'Mid')
        leaf = _location(12, 'Leaf')
        locations_by_id = {root.id: root, mid.id: mid, leaf.id: leaf}
        edges = [(root.id, mid.id), (mid.id, leaf.id)]
        tree = build_location_tree(locations_by_id, zone_edges=edges)
        self.assertEqual(len(tree), 1)
        self.assertEqual(tree[0]['id'], root.id)
        self.assertEqual(len(tree[0]['children']), 1)
        self.assertEqual(tree[0]['children'][0]['id'], mid.id)
        self.assertEqual(tree[0]['children'][0]['children'][0]['id'], leaf.id)


class LocationListPrefetchTests(TestCase):
    def test_list_query_count_does_not_grow_with_rows(self):
        for i in range(12):
            loc = Location.objects.create(
                id=3100 + i, name=f'Prefetch {i}', visible=True,
            )
            LocationRoleAssignment.objects.create(
                location=loc, role=LocationRole.STORAGE,
            )
        with CaptureQueriesContext(connection) as ctx:
            resp = location_list_api(
                RequestFactory().get('/container/locations/'),
            )
        self.assertEqual(resp.status_code, 200)
        self.assertLessEqual(len(ctx.captured_queries), 10)
