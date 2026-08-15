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
