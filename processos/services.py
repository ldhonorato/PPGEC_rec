from datetime import timedelta

from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from .models import Aluno, Docente, Processo, User


def processos_atrasados_base_queryset():
    return Processo.objects.filter(
        prazo_limite__lt=timezone.localdate(),
    ).exclude(status=Processo.StatusProcesso.FINALIZADO)


def processos_atrasados_queryset(user):
    queryset = processos_atrasados_base_queryset()
    if not user.is_authenticated:
        return queryset.none()

    if user.tipo_usuario == User.TipoUsuario.SERVIDOR:
        return queryset

    if user.tipo_usuario == User.TipoUsuario.DOCENTE:
        is_coordenador = Docente.objects.filter(pk=user.pk, coordenador=True).exists()
        if is_coordenador:
            return queryset

        orientandos = Aluno.objects.filter(orientador=user).values("id")
        return queryset.filter(
            Q(usuario_criado_por=user)
            | Q(usuario_criado_por__in=orientandos)
            | Q(setor_atual__nome__icontains="pleno")
        )

    return queryset.filter(usuario_criado_por=user)


def processos_atrasados_url(user):
    if user.is_authenticated and user.tipo_usuario in {User.TipoUsuario.SERVIDOR, User.TipoUsuario.DOCENTE}:
        if user.tipo_usuario == User.TipoUsuario.SERVIDOR or Docente.objects.filter(
            pk=user.pk,
            coordenador=True,
        ).exists():
            return f"{reverse('coordenacao_processos')}?atrasados=1"
    return f"{reverse('menu_meus_processos')}?my_atrasados=1"


def prazo_limite_padrao(tipo_processo, data_base=None):
    data_base = data_base or timezone.localdate()
    return data_base + timedelta(days=Processo.prazo_dias_para_tipo(tipo_processo))
