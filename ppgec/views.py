from django.conf import settings
from django.http import JsonResponse


def version_view(request):
    return JsonResponse(
        {
            "version": settings.APP_VERSION,
            "revision": settings.APP_REVISION,
            "build_run_id": settings.APP_BUILD_RUN_ID,
        }
    )
