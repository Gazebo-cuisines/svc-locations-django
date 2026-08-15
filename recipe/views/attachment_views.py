from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from locations.utils.api_response import api_error, api_success
from recipe.attachments import (
    AttachmentError,
    attachment_dict,
    delete_attachment,
    list_attachments,
    upload_attachment,
)
from recipe.models import RecipeAttachment, RecipeAttachmentKind, RecipeVersion
from recipe.permissions import gate_recipe_write
from recipe.views.helpers import actor, audit, version_locked_response


@require_http_methods(['GET', 'POST'])
@csrf_exempt
@gate_recipe_write
def recipe_version_attachments_api(request, pk: int):
    try:
        version = RecipeVersion.objects.select_related('recipe').get(pk=pk)
    except RecipeVersion.DoesNotExist:
        return api_error('Recipe version not found.', status_code=404)

    if request.method == 'GET':
        data = list_attachments(version)
        return api_success(
            'Attachments fetched successfully.',
            {'count': len(data), 'results': data},
        )

    locked = version_locked_response(version)
    if locked:
        return locked

    uploaded = request.FILES.get('file') or request.FILES.get('image')
    if not uploaded:
        return api_error('File is required (multipart field: file).', status_code=400)

    sub, _ = actor(request)
    try:
        data = upload_attachment(
            version,
            uploaded_file=uploaded,
            kind=request.POST.get('kind') or RecipeAttachmentKind.STEP,
            component_id=request.POST.get('component_id'),
            caption=request.POST.get('caption'),
            sort_order=request.POST.get('sort_order'),
            uploaded_by_sub=sub,
        )
    except AttachmentError as exc:
        return api_error(str(exc), status_code=400)

    audit(
        request,
        product_id=version.recipe.product_id,
        entity='recipe_attachment',
        action='create',
        before_data=None,
        after_data=data,
    )
    return api_success('Attachment uploaded successfully.', data, status_code=201)


@require_http_methods(['DELETE'])
@csrf_exempt
@gate_recipe_write
def recipe_attachment_detail_api(request, pk: int):
    try:
        row = RecipeAttachment.objects.select_related(
            'recipe_version__recipe',
        ).get(pk=pk)
    except RecipeAttachment.DoesNotExist:
        return api_error('Attachment not found.', status_code=404)

    locked = version_locked_response(row.recipe_version)
    if locked:
        return locked

    before_data = attachment_dict(row)
    product_id = row.recipe_version.recipe.product_id
    delete_attachment(row)
    audit(
        request,
        product_id=product_id,
        entity='recipe_attachment',
        action='delete',
        before_data=before_data,
        after_data=None,
    )
    return api_success('Attachment deleted successfully.', data=None)
