from django.http import JsonResponse


def api_success(message: str, data=None, *, status_code: int = 200) -> JsonResponse:
    return JsonResponse(
        {
            'status': 'success',
            'message': message,
            'data': data,
        },
        status=status_code,
    )


def api_error(message: str, data=None, *, status_code: int = 400) -> JsonResponse:
    return JsonResponse(
        {
            'status': 'error',
            'message': message,
            'data': data,
        },
        status=status_code,
    )
