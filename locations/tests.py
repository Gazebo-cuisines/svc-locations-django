from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from locations.models import LocationRelationType
from locations.services.hierarchy import assert_valid_edge


class HierarchyValidationTests(SimpleTestCase):
    def test_self_edge_rejected(self):
        with self.assertRaises(ValidationError):
            assert_valid_edge(3, 3, LocationRelationType.ZONE_GROUP)
