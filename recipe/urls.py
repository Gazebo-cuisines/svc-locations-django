from django.urls import path

from recipe.views.approval_views import (
    recipe_version_approve_api,
    recipe_version_history_api,
    recipe_version_reject_api,
    recipe_version_submit_api,
)
from recipe.views.attachment_views import (
    recipe_attachment_detail_api,
    recipe_version_attachments_api,
)
from recipe.views.audit_views import recipe_audit_api
from recipe.views.component_views import (
    recipe_component_collection_api,
    recipe_component_detail_api,
)
from recipe.views.recipe_views import (
    recipe_by_product_api,
    recipe_collection_api,
    recipe_detail_api,
    recipe_product_tree_api,
)
from recipe.views.version_views import (
    recipe_version_activate_api,
    recipe_version_collection_api,
    recipe_version_detail_api,
)

urlpatterns = [
    path('product/<int:product_id>/tree/', recipe_product_tree_api, name='recipe-product-tree'),
    path('product/<int:product_id>/', recipe_by_product_api, name='recipe-by-product'),
    path('', recipe_collection_api, name='recipe-collection'),
    path('<int:pk>/audit/', recipe_audit_api, name='recipe-audit'),
    path('<int:pk>/', recipe_detail_api, name='recipe-detail'),
    path('<int:pk>/versions/', recipe_version_collection_api, name='recipe-version-collection'),
    path('versions/<int:pk>/', recipe_version_detail_api, name='recipe-version-detail'),
    path('versions/<int:pk>/submit/', recipe_version_submit_api, name='recipe-version-submit'),
    path('versions/<int:pk>/approve/', recipe_version_approve_api, name='recipe-version-approve'),
    path('versions/<int:pk>/reject/', recipe_version_reject_api, name='recipe-version-reject'),
    path('versions/<int:pk>/history/', recipe_version_history_api, name='recipe-version-history'),
    path('versions/<int:pk>/activate/', recipe_version_activate_api, name='recipe-version-activate'),
    path('versions/<int:pk>/attachments/', recipe_version_attachments_api, name='recipe-version-attachments'),
    path('attachments/<int:pk>/', recipe_attachment_detail_api, name='recipe-attachment-detail'),
    path('versions/<int:pk>/components/', recipe_component_collection_api, name='recipe-component-collection'),
    path('components/<int:pk>/', recipe_component_detail_api, name='recipe-component-detail'),
]
