from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import (
    Aluno,
    Docente,
    Documento,
    Processo,
    Setor,
    TramitacaoProcesso,
    User,
)


@admin.register(User)
class UserAdmin(BaseUserAdmin):
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
class AlunoAdmin(admin.ModelAdmin):
    list_display = ("email", "nome", "ingresso", "orientador", "matricula", "is_active")
    list_filter = ("ingresso", "is_active")
    search_fields = ("email", "nome", "matricula")
    autocomplete_fields = ("orientador",)


@admin.register(Docente)
class DocenteAdmin(admin.ModelAdmin):
    list_display = ("email", "nome", "externo", "permanente", "coordenador", "is_active")
    list_filter = ("externo", "permanente", "coordenador", "is_active")
    search_fields = ("email", "nome")


@admin.register(Setor)
class SetorAdmin(admin.ModelAdmin):
    list_display = ("nome", "ativo")
    list_filter = ("ativo",)
    search_fields = ("nome", "descricao")


@admin.register(Processo)
class ProcessoAdmin(admin.ModelAdmin):
    list_display = ("numero", "assunto", "tipo", "status", "setor_atual", "data_criacao")
    list_filter = ("tipo", "status", "prioridade", "setor_atual")
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
