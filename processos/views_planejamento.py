from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.views.generic import ListView

from .forms import MetaPlanejamentoEstrategicoForm
from .models import MetaPlanejamentoEstrategico, Setor, SetorMembro, User


def setores_estrategicos_do_usuario(user):
    if not user.is_authenticated:
        return Setor.objects.none()
    return Setor.objects.filter(
        ativo=True, tipo=Setor.TipoSetor.ESTRATEGICO,
        membros__usuario=user, membros__data_saida__isnull=True,
    ).distinct()


def pode_visualizar_metas_planejamento(user):
    if not user.is_authenticated:
        return False
    if user.tipo_usuario in (User.TipoUsuario.DOCENTE, *User.tipos_com_acesso_servidor()):
        return True
    return SetorMembro.objects.filter(
        usuario=user, data_saida__isnull=True, setor__ativo=True,
    ).exists()


class MetasPlanejamentoListView(LoginRequiredMixin, ListView):
    model = MetaPlanejamentoEstrategico
    template_name = "processos/metas_planejamento.html"
    context_object_name = "metas"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not pode_visualizar_metas_planejamento(request.user):
            raise PermissionDenied("Você não tem acesso às metas do planejamento estratégico.")
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return super().get_queryset().select_related("setor")

    def get_form(self, data=None):
        return MetaPlanejamentoEstrategicoForm(
            data=data, setores=setores_estrategicos_do_usuario(self.request.user),
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        setores = setores_estrategicos_do_usuario(self.request.user)
        context["pode_criar_meta"] = setores.exists()
        context["form"] = kwargs.get("form") or self.get_form()
        return context

    def post(self, request, *args, **kwargs):
        if not setores_estrategicos_do_usuario(request.user).exists():
            raise PermissionDenied("Apenas membros de setores estratégicos podem criar metas.")
        form = self.get_form(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Meta do planejamento estratégico criada com sucesso.")
            return redirect("metas_planejamento")
        self.object_list = self.get_queryset()
        return self.render_to_response(self.get_context_data(form=form))
