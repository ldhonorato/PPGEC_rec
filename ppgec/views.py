import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordResetForm
from django.contrib.auth.views import LogoutView, PasswordResetView
from django.core.mail import EmailMultiAlternatives
from django.http import JsonResponse
from django.shortcuts import redirect
from django.template import loader


password_reset_logger = logging.getLogger("acadflow.password_reset")


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

