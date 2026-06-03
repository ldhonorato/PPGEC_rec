from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.hashers import identify_hasher

from .models import (
    AlteracaoAluno,
    Aluno,
    Docente,
    Documento,
    Processo,
    Setor,
    TrajetoriaAcademica,
    TramitacaoProcesso,
    User,
    EstagioDocencia,
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
    list_display = ("email", "nome", "tipo_usuario", "is_staff", "is_active")
    list_filter = ("tipo_usuario", "is_staff", "is_superuser", "is_active")
    search_fields = ("email", "nome")

    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Informacoes pessoais", {"fields": ("nome", "tipo_usuario")}),
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
                "fields": ("email", "nome", "tipo_usuario", "password1", "password2"),
            },
        ),
    )


@admin.register(Aluno)
class AlunoAdmin(EnsurePasswordHashedAdminMixin, admin.ModelAdmin):
    list_display = (
        "email",
        "nome",
        "status_aluno",
        "matricula",
        "is_active",
    )
    list_filter = (
        "status_aluno",
        "is_active",
    )
    search_fields = ("email", "nome", "matricula")


@admin.register(Docente)
class DocenteAdmin(EnsurePasswordHashedAdminMixin, admin.ModelAdmin):
    list_display = ("email", "nome", "externo", "permanente", "coordenador", "is_active")
    list_filter = ("externo", "permanente", "coordenador", "is_active")
    search_fields = ("email", "nome")


@admin.register(TrajetoriaAcademica)
class TrajetoriaAcademicaAdmin(admin.ModelAdmin):
    list_display = ("aluno", "nivel_curso", "status", "ingresso", "prazo_qualificacao", "prazo_defesa")
    list_filter = ("nivel_curso", "status", "reingressante")
    search_fields = ("aluno__nome", "aluno__email")
    autocomplete_fields = ("aluno", "orientador", "coorientador")


@admin.register(Setor)
class SetorAdmin(admin.ModelAdmin):
    list_display = ("nome", "ativo")
    list_filter = ("ativo",)
    search_fields = ("nome", "descricao")


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


#commit de teste 1

@admin.register(EstagioDocencia)
class EstagioDocenciaAdmin(admin.ModelAdmin):
    # 1. list_display: Colocamos as novidades! A secretaria bate o olho e já vê 
    # o status, o número do processo e se o relatório já foi entregue.
    list_display = (
        "trajetoria", 
        "supervisor", 
        "status", 
        "processo_vinculado", 
        "orientador_ciente", 
        "relatorio_entregue", 
        "dispensado"
    )
    
    # 2. list_filter: Agora a barra lateral direita é uma arma poderosa!
    # A secretaria pode clicar em "Status: PENDENTE_RELATORIO" e ver todo mundo que tá devendo.
    list_filter = (
        "status", 
        "dispensado", 
        "orientador_ciente", 
        "relatorio_entregue", 
        "inicio"
    )
    
    # 3. search_fields: Olha a mágica dos "__" (duplo underline) de novo!
    # Adicionamos "processo_vinculado__numero". Isso significa que se a coordenação mandar
    # um e-mail dizendo "Veja o estágio do processo 202606-000005", você joga na barra e acha!
    search_fields = (
        "trajetoria__aluno__nome", 
        "supervisor__nome", 
        "processo_vinculado__numero"
    )
    
    # 4. autocomplete_fields: Adicionamos o "processo_vinculado" aqui também.
    # Como a universidade vai ter milhares de processos gerados no ano, isso cria uma
    # barrinha de pesquisa super leve na hora de vincular, em vez de travar o site.
    autocomplete_fields = ("trajetoria", "supervisor", "processo_vinculado")