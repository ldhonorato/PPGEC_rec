from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.views.generic import DeleteView, FormView, ListView

from .forms import AcaoPlanejamentoEstrategicoForm, MetaPlanejamentoEstrategicoForm
from .models import AcoesPlanejamentoEstrategico, MetaPlanejamentoEstrategico, Setor, SetorMembro, User
from .planejamento_dashboard import montar_cronograma, montar_painel


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
        self.setor_selecionado = None
        setor_id = self.request.GET.get("setor")
        if setor_id:
            # Um filtro inválido não deve apresentar indicadores globais.
            if not setor_id.isascii() or not setor_id.isdecimal() or len(setor_id) > 18:
                raise Http404("Setor não encontrado.")
            self.setor_selecionado = get_object_or_404(
                self.get_setores_filtro(), pk=int(setor_id),
            )
        queryset = super().get_queryset()
        if self.setor_selecionado:
            queryset = queryset.filter(setor=self.setor_selecionado)
        return queryset.select_related("setor").prefetch_related(
            "acoes_planejamento_estrategico",
        ).order_by("setor__nome", "setor_id", "periodo_vigente", "categoria", "pk")

    def get_setores_filtro(self):
        return Setor.objects.filter(
            Q(tipo=Setor.TipoSetor.ESTRATEGICO, ativo=True) |
            Q(metas_planejamento_estrategico__isnull=False),
        ).distinct().order_by("nome", "pk")

    def get_form(self, data=None):
        return MetaPlanejamentoEstrategicoForm(
            data=data, setores=setores_estrategicos_do_usuario(self.request.user),
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        setores = setores_estrategicos_do_usuario(self.request.user)
        context["setores_editaveis"] = set(setores.values_list("pk", flat=True))
        context["pode_criar_meta"] = bool(context["setores_editaveis"])
        context["form"] = kwargs.get("form") or self.get_form()
        context["setores_filtro"] = self.get_setores_filtro()
        context["setor_selecionado"] = self.setor_selecionado
        context.update(montar_painel(list(self.object_list)))
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


class PlanejamentoAcompanhamentoView(MetasPlanejamentoListView):
    template_name = "processos/planejamento_acompanhamento.html"
    http_method_names = ["get", "head", "options"]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["acompanhamento"] = True
        context.update(montar_cronograma(context["acoes"], context["hoje"]))
        context["acoes_atencao"] = sorted(
            [acao for acao in context["acoes"] if acao.atrasada or acao.proxima or acao.sem_cronograma],
            key=lambda acao: (not acao.atrasada, not acao.proxima, acao.data_termino_planejado or context["hoje"], acao.pk),
        )
        return context


class AcaoPlanejamentoPermissaoMixin(LoginRequiredMixin):
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        self.meta = get_object_or_404(MetaPlanejamentoEstrategico, pk=kwargs["meta_pk"])
        if not setores_estrategicos_do_usuario(request.user).filter(pk=self.meta.setor_id).exists():
            raise PermissionDenied("Apenas membros ativos do setor estratégico podem gerenciar suas ações.")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["meta"] = self.meta
        return context

    def get_acao(self):
        return get_object_or_404(
            AcoesPlanejamentoEstrategico,
            pk=self.kwargs["pk"], meta_planejamento_estrategico=self.meta,
        )


class AcaoPlanejamentoFormView(AcaoPlanejamentoPermissaoMixin, FormView):
    template_name = "processos/acao_planejamento_form.html"
    form_class = AcaoPlanejamentoEstrategicoForm
    success_url = reverse_lazy("metas_planejamento")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        if "pk" in self.kwargs:
            kwargs["instance"] = self.get_acao()
        return kwargs

    def form_valid(self, form):
        form.instance.meta_planejamento_estrategico = self.meta
        form.save()
        messages.success(self.request, "Ação salva com sucesso.")
        return super().form_valid(form)


class AcaoPlanejamentoDeleteView(AcaoPlanejamentoPermissaoMixin, DeleteView):
    template_name = "processos/acao_planejamento_confirm_delete.html"
    success_url = reverse_lazy("metas_planejamento")

    def get_object(self, queryset=None):
        return self.get_acao()

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, "Ação removida com sucesso.")
        return response
