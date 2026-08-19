from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.hashers import identify_hasher

from .models import (
    AlteracaoMatricula,
    AlteracaoAluno,
    Aluno,
    AulaPresencialOferta,
    Disciplina,
    DisponibilidadeSala,
    DisciplinaTrajetoria,
    Docente,
    Documento,
    EncontroOferta,
    ItemSolicitacaoMatricula,
    GrupoLimiteHorasComplementares,
    LancamentoHorasComplementares,
    NormaHorasComplementares,
    Polo,
    PublicacaoTrajetoria,
    MembroBanca,
    OfertaDisciplina,
    PeriodoLetivo,
    Processo,
    ReservaAmbiente,
    Sala,
    Setor,
    SetorMembro,
    SolicitacaoMatricula,
    SolicitacaoBanca,
    TipoAtividadeHorasComplementares,
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
    list_display = ("email", "nome", "tipo_usuario", "polo_atuacao", "is_staff", "is_active")
    list_filter = ("tipo_usuario", "polo_atuacao", "is_staff", "is_superuser", "is_active")
    search_fields = ("email", "nome")

    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Informações pessoais", {"fields": ("nome", "tipo_usuario", "polo_atuacao")}),
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
        "sexo_atribuido_nascimento",
        "status_aluno",
        "matricula",
        "is_active",
    )
    list_filter = (
        "polo_atuacao",
        "sexo_atribuido_nascimento",
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


@admin.register(Disciplina)
class DisciplinaAdmin(admin.ModelAdmin):
    list_display = ("codigo", "nome", "tipo", "creditos", "carga_horaria", "ativa")
    list_filter = ("ativa", "tipo")
    search_fields = ("codigo", "nome", "tipo", "ementa", "bibliografia")


class EncontroOfertaInline(admin.TabularInline):
    model = EncontroOferta
    extra = 0
    max_num = 2


class AulaPresencialOfertaInline(admin.TabularInline):
    model = AulaPresencialOferta
    extra = 0
    autocomplete_fields = ("sala", "reserva", "criado_por")


@admin.register(PeriodoLetivo)
class PeriodoLetivoAdmin(admin.ModelAdmin):
    list_display = ("nome", "status_atual_display", "data_inicio", "data_fim", "prazo_cadastro_disciplinas", "matricula_inicio", "matricula_fim")
    search_fields = ("nome",)
    readonly_fields = ("criado_em", "atualizado_em")
    autocomplete_fields = ("criado_por", "encerrado_manualmente_por")


@admin.register(OfertaDisciplina)
class OfertaDisciplinaAdmin(admin.ModelAdmin):
    list_display = ("disciplina", "periodo", "docente_responsavel", "docente_colaborador", "modalidade", "vagas_regulares", "vagas_especiais")
    list_filter = ("periodo", "modalidade")
    search_fields = ("disciplina__codigo", "disciplina__nome", "docente_responsavel__nome", "docente_colaborador__nome")
    autocomplete_fields = ("periodo", "disciplina", "docente_responsavel", "docente_colaborador", "criada_por")
    inlines = [EncontroOfertaInline, AulaPresencialOfertaInline]


@admin.register(SolicitacaoMatricula)
class SolicitacaoMatriculaAdmin(admin.ModelAdmin):
    list_display = ("aluno", "periodo", "tipo_aluno", "status", "solicitada_em")
    list_filter = ("periodo", "tipo_aluno", "status")
    search_fields = ("aluno__nome", "aluno__email", "aluno__matricula")
    autocomplete_fields = ("periodo", "aluno")
    readonly_fields = ("criado_em", "atualizado_em")


@admin.register(ItemSolicitacaoMatricula)
class ItemSolicitacaoMatriculaAdmin(admin.ModelAdmin):
    list_display = ("solicitacao", "oferta", "status", "incluido_na_fase", "solicitado_em", "indeferido_em")
    list_filter = ("status", "incluido_na_fase", "oferta__periodo")
    search_fields = ("solicitacao__aluno__nome", "oferta__disciplina__nome", "oferta__disciplina__codigo")
    autocomplete_fields = ("solicitacao", "oferta", "indeferido_por")
    readonly_fields = ("solicitado_em", "atualizado_em")


@admin.register(AlteracaoMatricula)
class AlteracaoMatriculaAdmin(admin.ModelAdmin):
    list_display = ("solicitacao", "acao", "fase", "oferta", "realizado_por", "criado_em")
    list_filter = ("acao", "fase", "solicitacao__periodo")
    search_fields = (
        "solicitacao__aluno__nome",
        "solicitacao__aluno__matricula",
        "oferta__disciplina__codigo",
        "oferta__disciplina__nome",
        "realizado_por__nome",
    )
    readonly_fields = (
        "solicitacao", "item", "oferta", "acao", "fase", "realizado_por",
        "estado_anterior", "estado_novo", "justificativa", "criado_em",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


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
    list_display = ("numero", "assunto", "aluno_interessado", "usuario_criado_por", "tipo", "status", "setor_atual", "data_criacao")
    list_filter = ("tipo", "status", "setor_atual")
    search_fields = ("numero", "assunto", "descricao", "aluno_interessado__nome", "usuario_criado_por__nome")
    autocomplete_fields = ("aluno_interessado", "usuario_criado_por", "setor_atual")
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


@admin.register(NormaHorasComplementares)
class NormaHorasComplementaresAdmin(admin.ModelAdmin):
    list_display = ("identificacao", "nome", "nivel_curso", "status", "inicio_vigencia", "fim_vigencia", "carga_horaria_exigida")
    list_filter = ("status", "nivel_curso")
    search_fields = ("identificacao", "nome", "descricao")


@admin.register(GrupoLimiteHorasComplementares)
class GrupoLimiteHorasComplementaresAdmin(admin.ModelAdmin):
    list_display = ("nome", "norma", "limite_maximo", "ordem")
    list_filter = ("norma",)
    search_fields = ("nome", "descricao", "norma__identificacao")
    autocomplete_fields = ("norma",)


@admin.register(TipoAtividadeHorasComplementares)
class TipoAtividadeHorasComplementaresAdmin(admin.ModelAdmin):
    list_display = ("nome", "norma", "grupo_limite", "unidade_calculo", "horas_por_unidade", "limite_individual", "ativo", "ordem")
    list_filter = ("norma", "grupo_limite", "ativo")
    search_fields = ("nome", "descricao", "norma__identificacao")
    autocomplete_fields = ("norma", "grupo_limite")


@admin.register(LancamentoHorasComplementares)
class LancamentoHorasComplementaresAdmin(admin.ModelAdmin):
    list_display = ("trajetoria", "aluno", "tipo_atividade", "horas_aprovadas", "status", "processo_origem", "criado_por", "criado_em")
    list_filter = ("status", "norma", "tipo_atividade")
    search_fields = ("trajetoria__aluno__nome", "trajetoria__aluno__email", "descricao", "processo_origem__numero")
    autocomplete_fields = (
        "trajetoria",
        "processo_origem",
        "tipo_atividade",
        "norma",
        "grupo_limite",
        "criado_por",
        "substitui_lancamento",
        "cancelado_por",
    )
    readonly_fields = ("criado_em", "atualizado_em", "horas_calculadas")


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

@admin.register(EstagioDocencia)
class EstagioDocenciaAdmin(admin.ModelAdmin):
    list_display = (
        "trajetoria", 
        "supervisor", 
        "status", 
        "processo_vinculado", 
        "inicio",
        "termino",
        "relatorio_pendente_ou_proximo"
    )
    
    list_filter = ("status", "inicio")
    
    search_fields = (
        "trajetoria__aluno__nome", 
        "supervisor", 
        "processo_vinculado__numero"
    )
    
    autocomplete_fields = ("trajetoria", "processo_vinculado")
