"""Standard API response helpers. Use for every endpoint."""

from typing import Any

from django.http import JsonResponse


def success_response(
    message: str,
    data: Any = None,
    *,
    status_code: int = 200,
) -> JsonResponse:
    """
    {
      "status": "success",
      "message": "Product recorded successfully.",
      "data": { "ref": 122 }
    }
    """
    return JsonResponse(
        {"status": "success", "message": message, "data": data},
        status=status_code,
    )


def error_response(
    message: str,
    data: Any = None,
    *,
    status_code: int = 400,
) -> JsonResponse:
    """
    {
      "status": "error",
      "message": "We couldn't save that product.",
      "data": null
    }
    """
    return JsonResponse(
        {"status": "error", "message": message, "data": data},
        status=status_code,
    )
