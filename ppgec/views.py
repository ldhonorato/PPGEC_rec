import logging
import math
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordResetForm
from django.contrib.auth.views import LoginView, LogoutView, PasswordResetView
from django.db import transaction
from django.core.mail import EmailMultiAlternatives
from django.http import JsonResponse
from django.shortcuts import redirect
from django.template import loader
from django.utils import timezone
from django.utils.crypto import salted_hmac

from processos.models import LoginThrottle


password_reset_logger = logging.getLogger("acadflow.password_reset")
authentication_logger = logging.getLogger("acadflow.authentication")


class RateLimitedLoginView(LoginView):
    """Login com bloqueio persistente por conta e por endereco de origem."""

    lockout_message = "Muitas tentativas de acesso. Aguarde alguns minutos e tente novamente."

    def _identifiers(self):
        email = self.request.POST.get("username", "").strip().casefold()
        remote_addr = self.request.META.get("REMOTE_ADDR", "unknown")
        values = [("ip", remote_addr)]
        if email:
            values.append(("account", email))
        return [
            (scope, salted_hmac("acadflow.login-throttle", f"{scope}:{value}").hexdigest())
            for scope, value in values
        ]

    def _active_lock(self):
        now = timezone.now()
        locks = LoginThrottle.objects.filter(
            scope__in=[scope for scope, _ in self._identifiers()],
            fingerprint__in=[fingerprint for _, fingerprint in self._identifiers()],
            locked_until__gt=now,
        ).values_list("locked_until", flat=True)
        return max(locks, default=None)

    def _record_failure(self):
        now = timezone.now()
        window = timedelta(seconds=settings.LOGIN_FAILURE_WINDOW_SECONDS)
        lockout = timedelta(seconds=settings.LOGIN_LOCKOUT_SECONDS)
        locked_until = None
        with transaction.atomic():
            for scope, fingerprint in self._identifiers():
                record, _ = LoginThrottle.objects.select_for_update().get_or_create(
                    scope=scope,
                    fingerprint=fingerprint,
                    defaults={"window_started_at": now},
                )
                if now - record.window_started_at >= window:
                    record.failure_count = 0
                    record.window_started_at = now
                    record.locked_until = None
                record.failure_count += 1
                if record.failure_count >= settings.LOGIN_MAX_FAILURES:
                    record.locked_until = now + lockout
                    locked_until = max(locked_until or record.locked_until, record.locked_until)
                record.save()
        return locked_until

    def _clear_failures(self):
        for scope, fingerprint in self._identifiers():
            LoginThrottle.objects.filter(scope=scope, fingerprint=fingerprint).delete()

    def _locked_response(self, form, locked_until):
        remaining = max(1, math.ceil((locked_until - timezone.now()).total_seconds()))
        form.add_error(None, self.lockout_message)
        response = self.render_to_response(self.get_context_data(form=form), status=429)
        response["Retry-After"] = str(remaining)
        return response

    def post(self, request, *args, **kwargs):
        form = self.get_form()
        locked_until = self._active_lock()
        if locked_until:
            authentication_logger.warning("login_blocked")
            return self._locked_response(form, locked_until)
        if form.is_valid():
            self._clear_failures()
            return self.form_valid(form)
        locked_until = self._record_failure()
        if locked_until:
            authentication_logger.warning("login_lockout_started")
            return self._locked_response(form, locked_until)
        return self.form_invalid(form)


class AuditedPasswordResetForm(PasswordResetForm):
    def get_users(self, email):
        User = get_user_model()
        email_field_name = User.get_email_field_name()
        usuarios = list(User._default_manager.filter(**{f"{email_field_name}__iexact": email}))
        if not usuarios:
            password_reset_logger.warning(
                "password_reset_no_account email=%s",
                email,
            )
            return []

        elegiveis = []
        for usuario in usuarios:
            senha_utilizavel = usuario.has_usable_password()
            if usuario.is_active and senha_utilizavel:
                password_reset_logger.info(
                    "password_reset_account_eligible user_id=%s email=%s",
                    usuario.pk,
                    email,
                )
                elegiveis.append(usuario)
            else:
                password_reset_logger.warning(
                    "password_reset_account_ineligible user_id=%s email=%s is_active=%s usable_password=%s",
                    usuario.pk,
                    email,
                    usuario.is_active,
                    senha_utilizavel,
                )
        return elegiveis

    def send_mail(
        self,
        subject_template_name,
        email_template_name,
        context,
        from_email,
        to_email,
        html_email_template_name=None,
    ):
        usuario = context["user"]
        password_reset_logger.info(
            "password_reset_email_attempt user_id=%s email=%s backend=%s",
            usuario.pk,
            to_email,
            settings.EMAIL_BACKEND,
        )
        subject = "".join(loader.render_to_string(subject_template_name, context).splitlines())
        body = loader.render_to_string(email_template_name, context)
        mensagem = EmailMultiAlternatives(subject, body, from_email, [to_email])
        if html_email_template_name:
            mensagem.attach_alternative(loader.render_to_string(html_email_template_name, context), "text/html")

        try:
            enviados = mensagem.send(fail_silently=False)
        except Exception:
            password_reset_logger.exception(
                "password_reset_email_failed user_id=%s email=%s backend=%s",
                usuario.pk,
                to_email,
                settings.EMAIL_BACKEND,
            )
            return

        if enviados:
            password_reset_logger.info(
                "password_reset_email_sent user_id=%s email=%s sent_count=%s",
                usuario.pk,
                to_email,
                enviados,
            )
        else:
            password_reset_logger.error(
                "password_reset_email_not_sent user_id=%s email=%s sent_count=0",
                usuario.pk,
                to_email,
            )


class AuditedPasswordResetView(PasswordResetView):
    form_class = AuditedPasswordResetForm

    def form_valid(self, form):
        password_reset_logger.info(
            "password_reset_requested email=%s remote_addr=%s",
            form.cleaned_data["email"],
            self.request.META.get("REMOTE_ADDR", "-"),
        )
        return super().form_valid(form)


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
