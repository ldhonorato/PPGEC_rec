from django.conf import settings
from django.contrib.auth.views import LogoutView
from django.http import JsonResponse
from django.shortcuts import redirect


class SafeLogoutView(LogoutView):
    """Redireciona visitantes ao login sem permitir logout autenticado via GET."""

    http_method_names = ["get", "post", "options"]

    def get(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(settings.LOGIN_URL)
        return self.http_method_not_allowed(request, *args, **kwargs)


def version_view(request):
    return JsonResponse(
        {
            "version": settings.APP_VERSION,
            "revision": settings.APP_REVISION,
            "build_run_id": settings.APP_BUILD_RUN_ID,
        }
    )
