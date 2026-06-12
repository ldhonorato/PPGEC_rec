from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.hashers import identify_hasher

from .models import (
    AlteracaoAluno,
    Aluno,
    DisponibilidadeSala,
    DisciplinaTrajetoria,
    Docente,
    Documento,
    Polo,
    PublicacaoTrajetoria,
    MembroBanca,
    Processo,
    ReservaAmbiente,
    Sala,
    Setor,
    SetorMembro,
    SolicitacaoBanca,
    TrajetoriaAcademica,
    TramitacaoProcesso,
    User,
)


class EnsurePasswordHashedAdminMixin:
    def _ensure_hashed_password(self, obj):
        password = getattr(obj, "password", "")
        if not password:
            return

        try:
            identify_hasher(password)
        except ValueError:
            obj.set_password(password)

    def save_model(self, request, obj, form, change):
        self._ensure_hashed_password(obj)
        super().save_model(request, obj, form, change)


@admin.register(User)
class UserAdmin(EnsurePasswordHashedAdminMixin, BaseUserAdmin):
    ordering = ("email",)
    list_display = ("email", "nome", "tipo_usuario", "polo_atuacao", "is_staff", "is_active")
    list_filter = ("tipo_usuario", "polo_atuacao", "is_staff", "is_superuser", "is_active")
    search_fields = ("email", "nome")

    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Informacoes pessoais", {"fields": ("nome", "tipo_usuario", "polo_atuacao")}),
        (
            "Permissoes",
            {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")},
        ),
        ("Datas importantes", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "nome", "tipo_usuario", "polo_atuacao", "password1", "password2"),
            },
        ),
    )


@admin.register(Aluno)
class AlunoAdmin(EnsurePasswordHashedAdminMixin, admin.ModelAdmin):
    list_display = (
        "email",
        "nome",
        "polo_atuacao",
        "status_aluno",
        "matricula",
        "is_active",
    )
    list_filter = (
        "polo_atuacao",
        "status_aluno",
        "is_active",
    )
    search_fields = ("email", "nome", "matricula")


@admin.register(Docente)
class DocenteAdmin(EnsurePasswordHashedAdminMixin, admin.ModelAdmin):
    list_display = ("email", "nome", "polo_atuacao", "externo", "permanente", "coordenador", "is_active")
    list_filter = ("polo_atuacao", "externo", "permanente", "coordenador", "is_active")
    search_fields = ("email", "nome")


@admin.register(TrajetoriaAcademica)
class TrajetoriaAcademicaAdmin(admin.ModelAdmin):
    list_display = ("aluno", "nivel_curso", "status", "ingresso", "prazo_qualificacao", "prazo_defesa")
    list_filter = ("nivel_curso", "status", "reingressante")
    search_fields = ("aluno__nome", "aluno__email")
    autocomplete_fields = ("aluno", "orientador", "coorientador")


@admin.register(PublicacaoTrajetoria)
class PublicacaoTrajetoriaAdmin(admin.ModelAdmin):
    list_display = ("titulo", "trajetoria", "tipo", "ano", "criado_por")
    list_filter = ("tipo", "ano")
    search_fields = ("titulo", "autores", "veiculo", "trajetoria__aluno__nome")
    autocomplete_fields = ("trajetoria", "criado_por")


@admin.register(DisciplinaTrajetoria)
class DisciplinaTrajetoriaAdmin(admin.ModelAdmin):
    list_display = ("nome", "trajetoria", "semestre", "conceito", "situacao")
    list_filter = ("situacao", "semestre")
    search_fields = ("nome", "codigo", "trajetoria__aluno__nome")
    autocomplete_fields = ("trajetoria",)


@admin.register(Setor)
class SetorAdmin(admin.ModelAdmin):
    list_display = ("nome", "tipo", "ativo")
    list_filter = ("tipo", "ativo")
    search_fields = ("nome", "descricao")


@admin.register(SetorMembro)
class SetorMembroAdmin(admin.ModelAdmin):
    list_display = ("setor", "usuario", "data_entrada", "data_saida", "designado_por")
    list_filter = ("setor", "data_saida")
    search_fields = ("setor__nome", "usuario__nome", "usuario__email")
    autocomplete_fields = ("setor", "usuario", "designado_por")


@admin.register(Processo)
class ProcessoAdmin(admin.ModelAdmin):
    list_display = ("numero", "assunto", "tipo", "status", "setor_atual", "data_criacao")
    list_filter = ("tipo", "status", "setor_atual")
    search_fields = ("numero", "assunto", "descricao")
    autocomplete_fields = ("usuario_criado_por", "setor_atual")
    readonly_fields = ("numero", "data_criacao", "atualizado_em", "finalizado_em")


@admin.register(Documento)
class DocumentoAdmin(admin.ModelAdmin):
    list_display = ("titulo", "processo", "enviado_por", "data_envio", "tipo_documento")
    list_filter = ("tipo_documento", "data_envio")
    search_fields = ("titulo", "texto", "processo__numero")
    autocomplete_fields = ("processo", "enviado_por")
    readonly_fields = ("data_envio",)


@admin.register(TramitacaoProcesso)
class TramitacaoProcessoAdmin(admin.ModelAdmin):
    list_display = (
        "processo",
        "setor_origem",
        "setor_destino",
        "encaminhado_por",
        "status_resultante",
        "data_encaminhamento",
    )
    list_filter = ("status_resultante", "setor_origem", "setor_destino")
    search_fields = ("processo__numero", "observacao")
    autocomplete_fields = ("processo", "setor_origem", "setor_destino", "encaminhado_por")
    readonly_fields = ("data_encaminhamento",)


@admin.register(AlteracaoAluno)
class AlteracaoAlunoAdmin(admin.ModelAdmin):
    list_display = ("aluno", "tipo", "alterado_por", "criado_em")
    list_filter = ("tipo", "criado_em")
    search_fields = ("aluno__nome", "aluno__email", "comentario", "valor_anterior", "valor_novo")
    autocomplete_fields = ("aluno", "alterado_por")
    readonly_fields = ("criado_em",)


class MembroBancaInline(admin.TabularInline):
    model = MembroBanca
    extra = 0


@admin.register(SolicitacaoBanca)
class SolicitacaoBancaAdmin(admin.ModelAdmin):
    list_display = ("aluno", "docente", "tipo_defesa", "status", "data_prevista", "finalizado_em")
    list_filter = ("tipo_defesa", "status", "data_prevista")
    search_fields = ("aluno__nome", "docente__nome", "titulo")
    autocomplete_fields = ("docente", "aluno", "trajetoria", "finalizado_por")
    readonly_fields = ("criado_em", "atualizado_em", "finalizado_em")
    inlines = [MembroBancaInline]


@admin.register(Polo)
class PoloAdmin(admin.ModelAdmin):
    list_display = ("nome", "ativo")
    list_filter = ("ativo",)
    search_fields = ("nome", "descricao")


@admin.register(Sala)
class SalaAdmin(admin.ModelAdmin):
    list_display = ("nome", "polo", "capacidade", "ativa")
    list_filter = ("polo", "ativa")
    search_fields = ("nome", "polo__nome")


@admin.register(DisponibilidadeSala)
class DisponibilidadeSalaAdmin(admin.ModelAdmin):
    list_display = ("sala", "dia_semana", "hora_inicio", "hora_fim")
    list_filter = ("sala__polo", "dia_semana")
    search_fields = ("sala__nome", "sala__polo__nome")


@admin.register(ReservaAmbiente)
class ReservaAmbienteAdmin(admin.ModelAdmin):
    list_display = ("sala", "docente", "tipo", "status", "inicio", "fim", "recorrente")
    list_filter = ("tipo", "status", "sala__polo", "recorrente")
    search_fields = ("sala__nome", "docente__nome", "titulo", "justificativa_exclusao")
    autocomplete_fields = ("sala", "docente", "criado_por", "excluida_por")
    readonly_fields = ("criado_em", "excluida_em")


#commit de teste 1
