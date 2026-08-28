import json

from django.db.models import Q
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from core.api_response import error_response, success_response
from core.maintenance import maintenance_dict
from users_rbac.audit import profile_snapshot, record_event
from users_rbac.auth import require_admin, require_auth
from users_rbac.grants import (
    apply_grants,
    extract_grants,
    is_admin_user,
    user_dict,
    validate_grants,
)
from users_rbac.models import RbacAuditAction, RbacUser
from users_rbac.photos import upload_photo
from users_rbac.presence import IDLE_AFTER, presence_dict
from users_rbac.services import create_identity, reset_password, set_active


def _parse_body(request):
    try:
        body = json.loads(request.body or b'{}')
    except json.JSONDecodeError:
        return None
    return body if isinstance(body, dict) else None


def _get_user(user_id: int) -> RbacUser | None:
    return RbacUser.objects.filter(pk=user_id).first()


@csrf_exempt
@require_GET
@require_auth
def me_view(request):
    data = user_dict(request.rbac_user)
    data['maintenance'] = maintenance_dict()
    return success_response(
        'Your profile was fetched.',
        data=data,
    )


@csrf_exempt
@require_GET
@require_admin
def presence_list(request):
    qs = RbacUser.objects.filter(last_seen_at__isnull=False).order_by('-last_seen_at')
    if request.GET.get('all') not in ('1', 'true', 'yes'):
        qs = qs.filter(last_seen_at__gte=timezone.now() - IDLE_AFTER)
    return success_response(
        'Active users fetched.',
        data=[presence_dict(row) for row in qs],
    )


@csrf_exempt
@require_http_methods(['GET', 'POST'])
@require_admin
def users_collection(request):
    if request.method == 'GET':
        qs = RbacUser.objects.all().prefetch_related(
            'departments',
            'production_access',
            'warehouse_access',
            'admin_access',
        )
        query = (request.GET.get('q') or '').strip()
        if query:
            qs = qs.filter(Q(username__icontains=query) | Q(display_name__icontains=query))
        department = (request.GET.get('department') or '').strip()
        if department:
            qs = qs.filter(departments__department=department)
        active = request.GET.get('is_active')
        if active in ('true', '1'):
            qs = qs.filter(is_active=True)
        elif active in ('false', '0'):
            qs = qs.filter(is_active=False)
        return success_response(
            'Users fetched successfully.',
            data=[user_dict(row) for row in qs.distinct()],
        )

    body = _parse_body(request)
    if body is None:
        return error_response('Invalid request body.', status_code=400)
    username = (body.get('username') or '').strip()
    password = body.get('password') or ''
    if not username or not password:
        return error_response('Username and password are required.', status_code=400)
    grants_payload = extract_grants(body)
    if grants_payload is not None:
        try:
            validate_grants(grants_payload)
        except ValueError as exc:
            return error_response(str(exc), status_code=400)
    try:
        user = create_identity(
            username,
            password,
            email=body.get('email'),
            display_name=body.get('display_name') or '',
            created_by_sub=request.rbac_user.cognito_sub,
        )
    except ValueError as exc:
        message = str(exc)
        status = 409 if 'already in use' in message else 400
        return error_response(message, status_code=status)
    if grants_payload is not None:
        apply_grants(user, grants_payload)
    record_event(
        request,
        action=RbacAuditAction.USER_CREATED,
        target=user,
        after_json=user_dict(user),
    )
    return success_response(
        'User created successfully.',
        data=user_dict(user),
        status_code=201,
    )


@csrf_exempt
@require_http_methods(['GET', 'PATCH'])
@require_admin
def user_detail(request, user_id: int):
    user = _get_user(user_id)
    if not user:
        return error_response("We couldn't find that user.", status_code=404)
    if request.method == 'GET':
        return success_response('User fetched successfully.', data=user_dict(user))

    body = _parse_body(request)
    if body is None:
        return error_response('Invalid request body.', status_code=400)
    before = profile_snapshot(user)
    if 'display_name' in body:
        user.display_name = (body.get('display_name') or '').strip()
        user.save(update_fields=['display_name', 'updated_at'])
    if 'is_active' in body:
        if not isinstance(body.get('is_active'), bool):
            return error_response('is_active must be true or false.', status_code=400)
        try:
            set_active(user, body['is_active'])
        except ValueError as exc:
            return error_response(str(exc), status_code=400)
    after = profile_snapshot(user)
    if before['display_name'] != after['display_name']:
        record_event(
            request,
            action=RbacAuditAction.USER_UPDATED,
            target=user,
            before_json=before,
            after_json=after,
        )
    if before['is_active'] != after['is_active']:
        record_event(
            request,
            action=(
                RbacAuditAction.USER_ENABLED
                if after['is_active']
                else RbacAuditAction.USER_DISABLED
            ),
            target=user,
            before_json=before,
            after_json=after,
        )
    return success_response('User updated successfully.', data=user_dict(user))


@csrf_exempt
@require_http_methods(['PUT'])
@require_admin
def user_grants(request, user_id: int):
    user = _get_user(user_id)
    if not user:
        return error_response("We couldn't find that user.", status_code=404)
    body = _parse_body(request)
    if body is None:
        return error_response('Invalid request body.', status_code=400)
    try:
        apply_grants(user, body, request=request)
    except ValueError as exc:
        return error_response(str(exc), status_code=400)
    return success_response('User permissions updated.', data=user_dict(user))


@csrf_exempt
@require_POST
@require_admin
def user_reset_password(request, user_id: int):
    user = _get_user(user_id)
    if not user:
        return error_response("We couldn't find that user.", status_code=404)
    body = _parse_body(request)
    if body is None:
        return error_response('Invalid request body.', status_code=400)
    try:
        reset_password(user, body.get('password') or '')
    except ValueError as exc:
        return error_response(str(exc), status_code=400)
    record_event(
        request,
        action=RbacAuditAction.USER_PASSWORD_RESET,
        target=user,
        detail_json={'reset': True},
    )
    return success_response('Password was reset.', data={'ref': user.id})


def _can_edit_photo(request, user: RbacUser) -> bool:
    if request.rbac_user.id == user.id:
        return True
    return is_admin_user(request.rbac_user)


@csrf_exempt
@require_POST
@require_auth
def me_photo(request):
    uploaded = request.FILES.get('file') or request.FILES.get('photo')
    if not uploaded:
        return error_response('Photo file is required.', status_code=400)
    try:
        upload_photo(request.rbac_user, uploaded)
    except ValueError as exc:
        return error_response(str(exc), status_code=400)
    return success_response(
        'Photo updated successfully.',
        data=user_dict(request.rbac_user),
    )


@csrf_exempt
@require_POST
@require_auth
def user_photo(request, user_id: int):
    user = _get_user(user_id)
    if not user:
        return error_response("We couldn't find that user.", status_code=404)
    if not _can_edit_photo(request, user):
        return error_response('You do not have permission to do that.', status_code=403)
    uploaded = request.FILES.get('file') or request.FILES.get('photo')
    if not uploaded:
        return error_response('Photo file is required.', status_code=400)
    try:
        upload_photo(user, uploaded)
    except ValueError as exc:
        return error_response(str(exc), status_code=400)
    return success_response('Photo updated successfully.', data=user_dict(user))
