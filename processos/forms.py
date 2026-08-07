import re
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from django import forms
from django.contrib.auth import password_validation
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import (
    Aluno,
    Disciplina,
    DisponibilidadeSala,
    DisciplinaTrajetoria,
    ComentarioProcesso,
    DeliberacaoProcesso,
    Documento,
    EncontroOferta,
    EstagioDocencia,
    MembroBanca,
    LancamentoHorasComplementares,
    OfertaDisciplina,
    PeriodoLetivo,
    Polo,
    PublicacaoTrajetoria,
    Processo,
    ReservaAmbiente,
    Sala,
    SolicitacaoAssinatura,
    SolicitacaoBanca,
    SolicitacaoMatricula,
    Setor,
    TipoAtividadeHorasComplementares,
    TrajetoriaAcademica,
    validar_cpf_brasileiro,
)


User = get_user_model()


MAX_DOCUMENTO_UPLOAD_SIZE = 5 * 1024 * 1024
ALLOWED_DOCUMENTO_EXTENSIONS = {
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".bmp",
    ".tif",
    ".tiff",
}
DOCUMENTO_UPLOAD_ACCEPT = ",".join(sorted(ALLOWED_DOCUMENTO_EXTENSIONS))
MAX_ASSINATURA_UPLOAD_SIZE = 10 * 1024 * 1024



class OpcoesVaziasNomeadas:
    """Troca o "---------" que o Django poe na primeira opcao dos selects.

    Sao 36 campos no sistema, e o problema nao e so o traco ser feio: ele apaga
    uma distincao que importa. A opcao vazia significa duas coisas diferentes
    conforme o campo:

      - num campo obrigatorio, e "voce ainda nao escolheu";
      - num campo opcional, e "nenhum" -- uma resposta valida, nao uma pendencia.

    Com "---------" nos dois casos, quem preenche nao sabe se pode seguir sem
    tocar naquele campo. Com "Selecione" e "Nenhum", sabe.

    O texto e generico de proposito. "Selecione a sala" exigiria acertar o
    artigo de cada rotulo, e um formulario com "Selecione a docente" e pior do
    que um sem artigo nenhum -- o rotulo logo acima ja diz do que se trata. Onde
    um texto proprio ajudar, basta atribui-lo depois do super().__init__.

    A troca acontece no acesso ao campo, e nao no __init__, porque varios
    formularios ajustam "required" no proprio __init__ -- depois, portanto, da
    chamada a super(). Feita no __init__, a escolha entre "Selecione" e "Nenhum"
    usaria o valor de antes do ajuste: em ReservaAmbienteForm, "docente" saia
    como "Nenhum" sendo obrigatorio. No acesso, o estado ja e o definitivo.
    """

    TEXTO_OBRIGATORIO = "Selecione"
    TEXTO_OPCIONAL = "Nenhum"

    def __getitem__(self, nome):
        campo = self.fields.get(nome)
        if campo is not None:
            self._nomear_opcao_vazia(campo)
        return super().__getitem__(nome)

    def _nomear_opcao_vazia(self, campo):
        escolhas = getattr(campo, "choices", None)
        if not escolhas:
            return
        escolhas = list(escolhas)
        if not escolhas or escolhas[0][0] != "" or "---" not in str(escolhas[0][1]):
            return

        texto = self.TEXTO_OBRIGATORIO if campo.required else self.TEXTO_OPCIONAL
        # ModelChoiceField reconstroi as choices a partir do queryset a cada
        # acesso, entao nele o texto tem de ir em empty_label; nos demais, a
        # lista e estatica e se troca direto.
        if hasattr(campo, "empty_label"):
            campo.empty_label = texto
        else:
            escolhas[0] = ("", texto)
            campo.choices = escolhas



class UserProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ["nome", "email"]


class PublicacaoTrajetoriaForm(forms.ModelForm):
    class Meta:
        model = PublicacaoTrajetoria
        fields = ["titulo", "tipo", "autores", "veiculo", "ano", "doi_url"]
        widgets = {
            "autores": forms.Textarea(attrs={"rows": 3}),
            "ano": forms.NumberInput(attrs={"min": "1900", "max": "2100"}),
        }


class DisciplinaTrajetoriaForm(forms.ModelForm):
    class Meta:
        model = DisciplinaTrajetoria
        fields = ["codigo", "nome", "semestre", "conceito", "creditos", "carga_horaria", "situacao"]
        widgets = {
            "creditos": forms.NumberInput(attrs={"min": "0"}),
            "carga_horaria": forms.NumberInput(attrs={"min": "0"}),
        }


class DisciplinaForm(OpcoesVaziasNomeadas, forms.ModelForm):
    class Meta:
        model = Disciplina
        fields = ["codigo", "nome", "tipo", "creditos", "carga_horaria", "pre_requisitos", "ementa", "bibliografia", "ativa"]
        widgets = {
            "creditos": forms.NumberInput(attrs={"min": "0"}),
            "carga_horaria": forms.NumberInput(attrs={"min": "0"}),
            "pre_requisitos": forms.Textarea(attrs={"rows": 3}),
            "ementa": forms.Textarea(attrs={"rows": 5}),
            "bibliografia": forms.Textarea(attrs={"rows": 5}),
        }


class PeriodoLetivoForm(forms.ModelForm):
    class Meta:
        model = PeriodoLetivo
        fields = [
            "nome",
            "data_inicio",
            "data_fim",
            "prazo_cadastro_disciplinas",
            "prazo_agendamento_aulas_presenciais",
            "matricula_inicio",
            "matricula_fim",
            "modificacao_inicio",
            "modificacao_fim",
        ]
        widgets = {
            "data_inicio": forms.DateInput(attrs={"type": "date"}),
            "data_fim": forms.DateInput(attrs={"type": "date"}),
            "prazo_cadastro_disciplinas": forms.DateInput(attrs={"type": "date"}),
            "prazo_agendamento_aulas_presenciais": forms.DateInput(attrs={"type": "date"}),
            "matricula_inicio": forms.DateInput(attrs={"type": "date"}),
            "matricula_fim": forms.DateInput(attrs={"type": "date"}),
            "modificacao_inicio": forms.DateInput(attrs={"type": "date"}),
            "modificacao_fim": forms.DateInput(attrs={"type": "date"}),
        }


class OfertaDisciplinaForm(OpcoesVaziasNomeadas, forms.ModelForm):
    DIAS_OFERTA_CHOICES = [
        (EncontroOferta.DiaSemana.SEGUNDA, "Segunda-feira"),
        (EncontroOferta.DiaSemana.TERCA, "Terça-feira"),
        (EncontroOferta.DiaSemana.QUARTA, "Quarta-feira"),
        (EncontroOferta.DiaSemana.QUINTA, "Quinta-feira"),
        (EncontroOferta.DiaSemana.SEXTA, "Sexta-feira"),
        (EncontroOferta.DiaSemana.SABADO, "Sábado"),
    ]

    dia_semana_1 = forms.ChoiceField(choices=DIAS_OFERTA_CHOICES, label="Dia da semana 1")
    hora_inicio_1 = forms.TimeField(label="Horário inicial 1", widget=forms.TimeInput(attrs={"type": "time"}))
    hora_fim_1 = forms.TimeField(label="Horário final 1", widget=forms.TimeInput(attrs={"type": "time"}))
    dia_semana_2 = forms.ChoiceField(
        choices=[("", "---------")] + DIAS_OFERTA_CHOICES,
        required=False,
        label="Dia da semana 2",
    )
    hora_inicio_2 = forms.TimeField(
        required=False,
        label="Horário inicial 2",
        widget=forms.TimeInput(attrs={"type": "time"}),
    )
    hora_fim_2 = forms.TimeField(
        required=False,
        label="Horário final 2",
        widget=forms.TimeInput(attrs={"type": "time"}),
    )

    class Meta:
        model = OfertaDisciplina
        fields = [
            "periodo",
            "disciplina",
            "docente_responsavel",
            "docente_colaborador",
            "modalidade",
            "vagas_regulares",
            "vagas_especiais",
        ]
        widgets = {
            "vagas_regulares": forms.NumberInput(attrs={"min": "0"}),
            "vagas_especiais": forms.NumberInput(attrs={"min": "0"}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.fields["periodo"].queryset = PeriodoLetivo.objects.order_by("-nome")
        self.fields["disciplina"].queryset = Disciplina.objects.filter(ativa=True).order_by("codigo", "nome")
        self.fields["docente_responsavel"].queryset = User.objects.filter(
            tipo_usuario=User.TipoUsuario.DOCENTE,
            is_active=True,
        ).order_by("nome")
        self.fields["docente_colaborador"].queryset = User.objects.filter(
            tipo_usuario=User.TipoUsuario.DOCENTE,
            is_active=True,
        ).order_by("nome")
        self.fields["docente_colaborador"].required = False
        if user and user.tipo_usuario == User.TipoUsuario.DOCENTE:
            if not getattr(getattr(user, "docente", None), "coordenador", False):
                self.fields["docente_responsavel"].queryset = User.objects.filter(pk=user.pk)
            self.fields["docente_responsavel"].initial = user
        if self.instance and self.instance.pk:
            encontros = list(self.instance.encontros.order_by("dia_semana", "hora_inicio"))
            for idx, encontro in enumerate(encontros[:2], start=1):
                self.fields[f"dia_semana_{idx}"].initial = encontro.dia_semana
                self.fields[f"hora_inicio_{idx}"].initial = encontro.hora_inicio
                self.fields[f"hora_fim_{idx}"].initial = encontro.hora_fim

    def clean(self):
        cleaned_data = super().clean()
        periodo = cleaned_data.get("periodo")
        if periodo and not periodo.aceita_ofertas:
            self.add_error("periodo", "O prazo de cadastro de disciplinas deste período está encerrado.")
        docente_responsavel = cleaned_data.get("docente_responsavel")
        docente_colaborador = cleaned_data.get("docente_colaborador")
        if docente_responsavel and docente_colaborador and docente_responsavel.pk == docente_colaborador.pk:
            self.add_error("docente_colaborador", "O segundo docente deve ser diferente do docente responsável.")

        encontros = []
        for idx in (1, 2):
            dia = cleaned_data.get(f"dia_semana_{idx}")
            inicio = cleaned_data.get(f"hora_inicio_{idx}")
            fim = cleaned_data.get(f"hora_fim_{idx}")
            preenchido = bool(dia or inicio or fim)
            if idx == 1 or preenchido:
                if dia in ("", None) or not inicio or not fim:
                    self.add_error(f"hora_inicio_{idx}", "Informe dia, horário inicial e horário final.")
                    continue
                if fim <= inicio:
                    self.add_error(f"hora_fim_{idx}", "O horário final deve ser posterior ao inicial.")
                    continue
                encontros.append((int(dia), inicio, fim))
        for i, encontro_a in enumerate(encontros):
            for encontro_b in encontros[i + 1:]:
                if encontro_a[0] == encontro_b[0] and encontro_a[1] < encontro_b[2] and encontro_a[2] > encontro_b[1]:
                    self.add_error("dia_semana_2", "Os encontros da oferta possuem choque de horário.")
        return cleaned_data

    def save(self, commit=True):
        oferta = super().save(commit=False)
        if self.user and not oferta.criada_por_id:
            oferta.criada_por = self.user
        if commit:
            oferta.save()
            oferta.encontros.all().delete()
            for idx in (1, 2):
                dia = self.cleaned_data.get(f"dia_semana_{idx}")
                inicio = self.cleaned_data.get(f"hora_inicio_{idx}")
                fim = self.cleaned_data.get(f"hora_fim_{idx}")
                if dia not in ("", None) and inicio and fim:
                    EncontroOferta.objects.create(
                        oferta=oferta,
                        dia_semana=int(dia),
                        hora_inicio=inicio,
                        hora_fim=fim,
                    )
        return oferta


class SolicitacaoMatriculaForm(forms.Form):
    matricula_vinculo = forms.BooleanField(
        required=False,
        label="Matrícula vínculo",
    )
    ofertas = forms.ModelMultipleChoiceField(
        queryset=OfertaDisciplina.objects.none(),
        label="Disciplinas",
        widget=forms.CheckboxSelectMultiple,
        required=False,
    )
    aceitar_lista_espera = forms.BooleanField(
        required=False,
        label=(
            "Estou ciente de que as matrículas serão processadas por ordem de inscrição "
            "e que posso ficar em lista de espera quando não houver vaga."
        ),
    )
    observacao = forms.CharField(required=False, label="Observação", widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, periodo=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.periodo = periodo
        if periodo:
            self.fields["ofertas"].queryset = (
                OfertaDisciplina.objects.filter(periodo=periodo)
                .select_related("disciplina", "docente_responsavel", "docente_colaborador")
                .prefetch_related("encontros")
                .order_by("disciplina__nome")
            )

    def clean(self):
        cleaned_data = super().clean()
        matricula_vinculo = cleaned_data.get("matricula_vinculo")
        ofertas = cleaned_data.get("ofertas")
        if not cleaned_data.get("aceitar_lista_espera"):
            self.add_error("aceitar_lista_espera", "Confirme a ciência para enviar a solicitação.")
        if matricula_vinculo or not ofertas:
            cleaned_data["matricula_vinculo"] = True
            cleaned_data["ofertas"] = []
        return cleaned_data


class SetorComissaoForm(forms.ModelForm):
    docentes = forms.ModelMultipleChoiceField(
        queryset=User.objects.filter(tipo_usuario=User.TipoUsuario.DOCENTE, is_active=True).order_by("nome", "email"),
        required=False,
        label="Docentes",
        widget=forms.CheckboxSelectMultiple,
    )
    servidores = forms.ModelMultipleChoiceField(
        queryset=User.objects.filter(tipo_usuario=User.TipoUsuario.SERVIDOR, is_active=True).order_by("nome", "email"),
        required=False,
        label="Servidores",
        widget=forms.CheckboxSelectMultiple,
    )
    alunos = forms.ModelMultipleChoiceField(
        queryset=User.objects.filter(tipo_usuario=User.TipoUsuario.ALUNO).order_by("nome", "email"),
        required=False,
        label="Alunos",
        widget=forms.CheckboxSelectMultiple,
    )

    class Meta:
        model = Setor
        fields = ["nome", "descricao", "email", "ativo", "docentes", "servidores", "alunos"]


def _validar_pdf_upload(arquivo):
    nome = Path(arquivo.name or "")
    if nome.suffix.lower() != ".pdf":
        raise forms.ValidationError("Envie um arquivo PDF.")
    if arquivo.size > MAX_ASSINATURA_UPLOAD_SIZE:
        raise forms.ValidationError("O PDF deve ter no máximo 10 MB.")


class SolicitacaoAssinaturaForm(OpcoesVaziasNomeadas, forms.ModelForm):
    class Meta:
        model = SolicitacaoAssinatura
        fields = [
            "destinatario_tipo",
            "docente",
            "setor",
            "tipo_documento",
            "numero_documento_sei",
            "numero_bloco_sei",
            "documento_pdf",
            "observacao",
        ]
        widgets = {
            "observacao": forms.Textarea(attrs={"rows": 3}),
            "documento_pdf": forms.FileInput(attrs={"accept": ".pdf"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["docente"].queryset = User.objects.filter(
            tipo_usuario=User.TipoUsuario.DOCENTE,
            is_active=True,
        ).order_by("nome", "email")
        self.fields["setor"].queryset = Setor.objects.filter(ativo=True).order_by("nome")
        for field_name in ["docente", "setor", "numero_documento_sei", "numero_bloco_sei", "documento_pdf"]:
            self.fields[field_name].required = False

    def clean_documento_pdf(self):
        arquivo = self.cleaned_data.get("documento_pdf")
        if arquivo:
            _validar_pdf_upload(arquivo)
        return arquivo


class AtenderSolicitacaoAssinaturaForm(forms.ModelForm):
    class Meta:
        model = SolicitacaoAssinatura
        fields = ["documento_assinado_pdf", "observacao_assinatura"]
        widgets = {
            "documento_assinado_pdf": forms.FileInput(attrs={"accept": ".pdf"}),
            "observacao_assinatura": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, solicitacao=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.solicitacao = solicitacao
        self.fields["documento_assinado_pdf"].required = bool(solicitacao and solicitacao.is_pdf)
        if solicitacao and not solicitacao.is_pdf:
            self.fields["documento_assinado_pdf"].widget = forms.HiddenInput()

    def clean_documento_assinado_pdf(self):
        arquivo = self.cleaned_data.get("documento_assinado_pdf")
        if arquivo:
            _validar_pdf_upload(arquivo)
        return arquivo

    def clean(self):
        cleaned_data = super().clean()
        if self.solicitacao and self.solicitacao.is_pdf and not cleaned_data.get("documento_assinado_pdf"):
            self.add_error("documento_assinado_pdf", "Anexe o PDF assinado.")
        return cleaned_data


class SalaForm(forms.ModelForm):
    class Meta:
        model = Sala
        fields = ["polo", "nome", "capacidade", "ativa"]

    def __init__(self, *args, can_choose_polo=False, include_ativa=True, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["polo"].queryset = Polo.objects.filter(ativo=True).order_by("nome")
        if not can_choose_polo:
            self.fields.pop("polo")
        if not include_ativa:
            self.fields.pop("ativa")


class DisponibilidadeSalaForm(OpcoesVaziasNomeadas, forms.ModelForm):
    class Meta:
        model = DisponibilidadeSala
        fields = ["sala", "dia_semana", "hora_inicio", "hora_fim"]
        widgets = {
            "hora_inicio": forms.TimeInput(attrs={"type": "time"}),
            "hora_fim": forms.TimeInput(attrs={"type": "time"}),
        }

    def __init__(self, *args, polo=None, **kwargs):
        super().__init__(*args, **kwargs)
        queryset = Sala.objects.filter(ativa=True).order_by("nome")
        if polo:
            queryset = queryset.filter(polo=polo)
        self.fields["sala"].queryset = queryset


class DisponibilidadeSalaLoteForm(forms.Form):
    dias_semana = forms.MultipleChoiceField(
        choices=DisponibilidadeSala.DiaSemana.choices,
        label="Dias da semana",
        widget=forms.CheckboxSelectMultiple,
    )
    hora_inicio = forms.TimeField(
        label="Hora de início",
        widget=forms.TimeInput(attrs={"type": "time"}),
    )
    hora_fim = forms.TimeField(
        label="Hora de fim",
        widget=forms.TimeInput(attrs={"type": "time"}),
    )

    def clean(self):
        cleaned_data = super().clean()
        hora_inicio = cleaned_data.get("hora_inicio")
        hora_fim = cleaned_data.get("hora_fim")
        if hora_inicio and hora_fim and hora_fim <= hora_inicio:
            self.add_error("hora_fim", "O horário final deve ser posterior ao horário inicial.")
        return cleaned_data

    def save(self, sala):
        disponibilidades = []
        for dia_semana in self.cleaned_data["dias_semana"]:
            disponibilidade = DisponibilidadeSala(
                sala=sala,
                dia_semana=int(dia_semana),
                hora_inicio=self.cleaned_data["hora_inicio"],
                hora_fim=self.cleaned_data["hora_fim"],
            )
            disponibilidade.save()
            disponibilidades.append(disponibilidade)
        return disponibilidades


class SolicitacaoBancaForm(OpcoesVaziasNomeadas, forms.ModelForm):
    aluno = forms.ModelChoiceField(queryset=Aluno.objects.none(), label="Aluno")
    trajetoria = forms.ModelChoiceField(queryset=TrajetoriaAcademica.objects.none(), label="Trajetória acadêmica")

    class Meta:
        model = SolicitacaoBanca
        fields = [
            "aluno",
            "trajetoria",
            "tipo_defesa",
            "titulo",
            "resumo",
            "palavras_chave",
            "data_prevista",
            "horario_previsto",
            "modalidade_local_link",
            "requisitos_cumpridos",
            "justificativa_excepcionalidade",
            "ciencia_recomendacao_mpf",
        ]
        widgets = {
            "data_prevista": forms.DateInput(attrs={"type": "date"}),
            "horario_previsto": forms.TimeInput(attrs={"type": "time"}),
            "resumo": forms.Textarea(attrs={"rows": 5}),
            "modalidade_local_link": forms.Textarea(attrs={"rows": 3}),
            "justificativa_excepcionalidade": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, docente=None, finalizar=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.docente = docente
        self.finalizar = finalizar
        if docente:
            self.instance.docente = docente

        alunos = Aluno.objects.none()
        trajetorias = TrajetoriaAcademica.objects.none()
        if docente:
            trajetorias = (
                TrajetoriaAcademica.objects.select_related("aluno", "orientador", "coorientador")
                .filter(status=TrajetoriaAcademica.Status.ATIVA)
                .filter(Q(orientador=docente) | Q(coorientador=docente))
                .order_by("aluno__nome", "-criado_em")
            )
            alunos = Aluno.objects.filter(trajetorias__in=trajetorias).distinct().order_by("nome")

        self.fields["aluno"].queryset = alunos
        self.fields["trajetoria"].queryset = trajetorias

        for field_name in [
            "titulo",
            "resumo",
            "palavras_chave",
            "data_prevista",
            "horario_previsto",
            "modalidade_local_link",
            "requisitos_cumpridos",
            "justificativa_excepcionalidade",
            "ciencia_recomendacao_mpf",
        ]:
            self.fields[field_name].required = False

        for papel, label in MembroBanca.Papel.choices:
            self.fields[f"membro_{papel}_nome"] = forms.CharField(label=f"{label} - Nome", required=False)
            self.fields[f"membro_{papel}_instituicao"] = forms.CharField(
                label=f"{label} - Instituição/IES",
                required=False,
            )
            self.fields[f"membro_{papel}_cpf"] = forms.CharField(label=f"{label} - CPF", required=False)

        if self.instance and self.instance.pk:
            for membro in self.instance.membros.all():
                prefixo = f"membro_{membro.papel}"
                self.fields[f"{prefixo}_nome"].initial = membro.nome
                self.fields[f"{prefixo}_instituicao"].initial = membro.instituicao
                self.fields[f"{prefixo}_cpf"].initial = membro.cpf

    def clean(self):
        cleaned_data = super().clean()
        aluno = cleaned_data.get("aluno")
        trajetoria = cleaned_data.get("trajetoria")
        tipo_defesa = cleaned_data.get("tipo_defesa")

        if aluno and trajetoria and trajetoria.aluno_id != aluno.id:
            self.add_error("trajetoria", "A trajetória selecionada não pertence ao aluno.")
        if trajetoria and self.docente:
            docente_vinculado = trajetoria.orientador_id == self.docente.id or trajetoria.coorientador_id == self.docente.id
            if not docente_vinculado:
                self.add_error("trajetoria", "Selecione uma trajetória sob sua orientação ou coorientação.")
        if trajetoria and tipo_defesa:
            if tipo_defesa == SolicitacaoBanca.TipoDefesa.DEFESA_MESTRADO and trajetoria.nivel_curso != Aluno.NivelCurso.MESTRADO:
                self.add_error("tipo_defesa", "Defesa de Mestrado exige trajetória de mestrado.")
            if tipo_defesa in {
                SolicitacaoBanca.TipoDefesa.QUALIFICACAO_DOUTORADO,
                SolicitacaoBanca.TipoDefesa.DEFESA_DOUTORADO,
            } and trajetoria.nivel_curso != Aluno.NivelCurso.DOUTORADO:
                self.add_error("tipo_defesa", "Solicitação de doutorado exige trajetória de doutorado.")

        if self.finalizar:
            self._validar_campos_obrigatorios_finalizacao(cleaned_data)
            self._validar_membros_finalizacao(cleaned_data, tipo_defesa)
        else:
            self._validar_cpfs_informados(cleaned_data)

        return cleaned_data

    def _validar_campos_obrigatorios_finalizacao(self, cleaned_data):
        for field_name in [
            "titulo",
            "resumo",
            "palavras_chave",
            "data_prevista",
            "horario_previsto",
            "modalidade_local_link",
        ]:
            value = cleaned_data.get(field_name)
            if value is None or (isinstance(value, str) and not value.strip()):
                self.add_error(field_name, "Campo obrigatório para finalizar.")
        if not cleaned_data.get("requisitos_cumpridos"):
            self.add_error("requisitos_cumpridos", "Confirme que o discente cumpre os requisitos.")
        if not cleaned_data.get("ciencia_recomendacao_mpf"):
            self.add_error("ciencia_recomendacao_mpf", "Confirme a ciência para finalizar.")

    def _validar_membros_finalizacao(self, cleaned_data, tipo_defesa):
        for papel in MembroBanca.papeis_para_tipo(tipo_defesa):
            prefixo = f"membro_{papel}"
            nome = (cleaned_data.get(f"{prefixo}_nome") or "").strip()
            instituicao = (cleaned_data.get(f"{prefixo}_instituicao") or "").strip()
            cpf = (cleaned_data.get(f"{prefixo}_cpf") or "").strip()
            membro_vazio = not any([nome, instituicao, cpf])
            if MembroBanca.papel_opcional(tipo_defesa, papel) and membro_vazio:
                continue
            if not nome:
                self.add_error(f"{prefixo}_nome", "Informe o nome.")
            if MembroBanca.exige_instituicao(papel) and not instituicao:
                self.add_error(f"{prefixo}_instituicao", "Informe a instituição/IES.")
            if MembroBanca.exige_cpf(tipo_defesa, papel) and not cpf:
                self.add_error(f"{prefixo}_cpf", "Informe o CPF.")
            elif cpf and not validar_cpf_brasileiro(cpf):
                self.add_error(f"{prefixo}_cpf", "Informe um CPF válido.")

    def _validar_cpfs_informados(self, cleaned_data):
        for papel, _label in MembroBanca.Papel.choices:
            cpf = (cleaned_data.get(f"membro_{papel}_cpf") or "").strip()
            if cpf and not validar_cpf_brasileiro(cpf):
                self.add_error(f"membro_{papel}_cpf", "Informe um CPF válido.")

    def save(self, commit=True, *, docente=None, status=SolicitacaoBanca.Status.RASCUNHO):
        solicitacao = super().save(commit=False)
        if docente:
            solicitacao.docente = docente
        solicitacao.status = status
        if commit:
            solicitacao.save()
            self.save_membros(solicitacao)
        return solicitacao

    def save_membros(self, solicitacao):
        solicitacao.membros.all().delete()
        for papel in MembroBanca.papeis_para_tipo(solicitacao.tipo_defesa):
            prefixo = f"membro_{papel}"
            nome = (self.cleaned_data.get(f"{prefixo}_nome") or "").strip()
            instituicao = (self.cleaned_data.get(f"{prefixo}_instituicao") or "").strip()
            cpf = (self.cleaned_data.get(f"{prefixo}_cpf") or "").strip()
            if nome or instituicao or cpf:
                MembroBanca.objects.create(
                    solicitacao=solicitacao,
                    papel=papel,
                    nome=nome,
                    instituicao=instituicao,
                    cpf=cpf,
                )


class ReservaAmbienteForm(OpcoesVaziasNomeadas, forms.Form):
    RECORRENCIA_NENHUMA = "NENHUMA"
    RECORRENCIA_DIARIA = "DIARIA"
    RECORRENCIA_SEMANAL = "SEMANAL"
    RECORRENCIA_MENSAL = "MENSAL"

    sala = forms.ModelChoiceField(queryset=Sala.objects.none(), label="Sala")
    docente = forms.ModelChoiceField(
        queryset=User.objects.filter(tipo_usuario=User.TipoUsuario.DOCENTE, is_active=True).order_by("nome"),
        required=False,
        label="Docente",
    )
    tipo = forms.ChoiceField(choices=ReservaAmbiente.TipoReserva.choices, label="Tipo de reserva")
    titulo = forms.CharField(max_length=255, required=False, label="Título")
    data_inicio = forms.DateField(
        label="Data de início",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    hora_inicio = forms.TimeField(
        label="Hora de início",
        widget=forms.TimeInput(attrs={"type": "time"}),
    )
    hora_fim = forms.TimeField(
        label="Hora de fim",
        widget=forms.TimeInput(attrs={"type": "time"}),
    )
    recorrencia = forms.ChoiceField(
        choices=(
            (RECORRENCIA_NENHUMA, "Não repetir"),
            (RECORRENCIA_DIARIA, "Diaria"),
            (RECORRENCIA_SEMANAL, "Semanal"),
            (RECORRENCIA_MENSAL, "Mensal"),
        ),
        label="Recorrência",
    )
    duracao_recorrencia_meses = forms.IntegerField(
        required=False,
        min_value=1,
        max_value=6,
        label="Duração da recorrência em meses",
        widget=forms.NumberInput(attrs={"min": "1", "max": "6"}),
    )

    def __init__(self, *args, user=None, polo=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        queryset = Sala.objects.filter(ativa=True, polo__ativo=True).select_related("polo").order_by("polo__nome", "nome")
        if polo:
            queryset = queryset.filter(polo=polo)
        self.fields["sala"].queryset = queryset
        if user and user.tipo_usuario == User.TipoUsuario.DOCENTE:
            self.fields["docente"].required = False
            self.fields["docente"].widget = forms.HiddenInput()
        else:
            self.fields["docente"].required = True

    def clean(self):
        cleaned_data = super().clean()
        data_inicio = cleaned_data.get("data_inicio")
        hora_inicio = cleaned_data.get("hora_inicio")
        hora_fim = cleaned_data.get("hora_fim")
        recorrencia = cleaned_data.get("recorrencia")
        duracao_recorrencia_meses = cleaned_data.get("duracao_recorrencia_meses")

        inicio = None
        if data_inicio and hora_inicio:
            inicio = datetime.combine(data_inicio, hora_inicio)
            if timezone.is_naive(inicio):
                inicio = timezone.make_aware(inicio)
            cleaned_data["inicio"] = inicio

        if data_inicio and hora_fim:
            fim = datetime.combine(data_inicio, hora_fim)
            if timezone.is_naive(fim):
                fim = timezone.make_aware(fim)
            cleaned_data["fim"] = fim

        if hora_inicio and hora_fim and hora_fim <= hora_inicio:
            self.add_error("hora_fim", "A hora de fim deve ser posterior a hora de início no mesmo dia.")
        if recorrencia != self.RECORRENCIA_NENHUMA:
            if not duracao_recorrencia_meses:
                self.add_error("duracao_recorrencia_meses", "Informe por quantos meses repetir.")
        else:
            cleaned_data["duracao_recorrencia_meses"] = None
        return cleaned_data


class ReservaAmbienteExclusaoForm(forms.Form):
    justificativa = forms.CharField(
        label="Justificativa",
        widget=forms.Textarea(attrs={"rows": 3}),
    )


class DocumentoCadastroForm(forms.Form):
    titulo = forms.CharField(max_length=255, label="Título")
    tipo_documento = forms.ChoiceField(
        choices=[("", "Selecione")] + list(Documento.TipoDocumento.choices),
        required=False,
        label="Tipo de documento",
    )
    arquivo = forms.FileField(
        required=True,
        label="Arquivo do documento",
        help_text="PDF, Office ou imagem. Tamanho máximo: 5 MB.",
        widget=forms.ClearableFileInput(attrs={"accept": DOCUMENTO_UPLOAD_ACCEPT}),
    )
    restricao_tipo = forms.ChoiceField(
        choices=Documento.RestricaoAcesso.choices,
        required=True,
        label="Documento restrito",
    )

    def clean_arquivo(self):
        arquivo = self.cleaned_data["arquivo"]
        if arquivo.size > MAX_DOCUMENTO_UPLOAD_SIZE:
            raise forms.ValidationError("O arquivo deve ter no máximo 5 MB.")

        extensao = Path(arquivo.name).suffix.lower()
        if extensao not in ALLOWED_DOCUMENTO_EXTENSIONS:
            raise forms.ValidationError(
                "Formato não permitido. Envie PDF, arquivos Office ou imagens."
            )

        return arquivo


class EncaminhamentoForm(OpcoesVaziasNomeadas, forms.Form):
    setor_destino = forms.ModelChoiceField(
        queryset=Setor.objects.none(),
        label="Setor de destino",
    )
    despacho = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        label="Despacho",
    )
    prazo_pleno = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
        label="Data limite para deliberação",
    )

    # Campo para capturar a data limite exata
    prazo_limite = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
        label="Data Limite",
    )

    def __init__(self, *args, current_setor_id=None, allowed_setor_ids=None, **kwargs):
        super().__init__(*args, **kwargs)
        queryset = Setor.objects.filter(ativo=True).order_by("nome")
        if allowed_setor_ids is not None:
            queryset = queryset.filter(id__in=allowed_setor_ids)
        if current_setor_id:
            queryset = queryset.exclude(id=current_setor_id)
        self.fields["setor_destino"].queryset = queryset

    def clean(self):
        cleaned_data = super().clean()
        setor = cleaned_data.get("setor_destino")
        if setor and "pleno" in setor.nome.lower() and not cleaned_data.get("prazo_pleno"):
            self.add_error("prazo_pleno", "Informe a data limite para deliberação no Pleno.")
        return cleaned_data


class ProcessoAberturaForm(forms.ModelForm):
    """Abertura de processo pelo requerente.

    Os textos de ajuda existem porque "Assunto" e "Descricao", lado a lado e sem
    explicacao, nao dizem o que muda de um para o outro -- e o assunto e o que a
    secretaria le primeiro na caixa, entao um assunto vago custa uma ida e volta.
    """

    class Meta:
        model = Processo
        fields = ["tipo", "assunto", "descricao"]
        widgets = {
            "descricao": forms.Textarea(attrs={"rows": 6}),
        }
        help_texts = {
            "tipo": "Define para onde o processo vai e que documentos serão exigidos.",
            "assunto": "Uma linha que identifique o pedido. É o que aparece na listagem.",
            "descricao": "Explique o que está pedindo e por quê. Inclua datas, disciplinas "
                         "ou prazos envolvidos.",
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        # O padrao do Django e "---------", que ocupa a primeira posicao da lista
        # sem dizer o que fazer com ela. Como "tipo" tem choices e nao e chave
        # estrangeira, o campo e um TypedChoiceField -- que nao tem empty_label;
        # a opcao vazia vem dentro de choices e e la que se troca.
        escolhas = list(self.fields["tipo"].choices)
        if escolhas and escolhas[0][0] == "":
            escolhas[0] = ("", "Selecione o tipo de requerimento")
            self.fields["tipo"].choices = escolhas
        self.fields["assunto"].widget.attrs.setdefault(
            "placeholder", "Ex.: Prorrogação de prazo para defesa"
        )


class SolicitarCienteOrientadorForm(forms.Form):
    mensagem_solicitacao = forms.CharField(
        required=False,
        label="Observação da solicitação",
        widget=forms.Textarea(attrs={"rows": 3}),
    )


class ManifestarCienteOrientadorForm(forms.Form):
    mensagem_manifestacao = forms.CharField(
        required=False,
        label="Mensagem da manifestação",
        widget=forms.Textarea(attrs={"rows": 3}),
    )


class ComentarioProcessoForm(forms.Form):
    tipo = forms.ChoiceField(
        choices=ComentarioProcesso.TipoComentario.choices,
        label="Finalidade do comentário",
        widget=forms.RadioSelect,
    )
    anonimo = forms.BooleanField(required=False, label="Comentário anônimo")
    texto = forms.CharField(
        label="Comentário",
        widget=forms.Textarea(attrs={"rows": 4}),
    )

    def __init__(self, *args, debate_aberto=False, **kwargs):
        super().__init__(*args, **kwargs)
        if debate_aberto:
            self.fields["tipo"].choices = [
                (ComentarioProcesso.TipoComentario.OBSERVACAO, "Registrar observação"),
                (ComentarioProcesso.TipoComentario.DEBATE, "Responder ao debate"),
            ]


class DeliberacaoProcessoForm(forms.Form):
    posicao = forms.ChoiceField(
        choices=DeliberacaoProcesso.Posicao.choices,
        label="Manifestação sobre o pleito",
        widget=forms.RadioSelect,
    )


class LancamentoHorasComplementaresForm(OpcoesVaziasNomeadas, forms.ModelForm):
    trajetoria = forms.ModelChoiceField(
        queryset=TrajetoriaAcademica.objects.none(),
        label="Trajetória acadêmica",
    )
    processo_origem = forms.ModelChoiceField(
        queryset=Processo.objects.none(),
        required=False,
        label="Processo de origem",
    )
    retificar_lancamento = forms.ModelChoiceField(
        queryset=LancamentoHorasComplementares.objects.none(),
        required=False,
        label="Retifica lançamento",
    )

    class Meta:
        model = LancamentoHorasComplementares
        fields = [
            "trajetoria",
            "processo_origem",
            "tipo_atividade",
            "descricao",
            "periodo_realizacao",
            "quantidade",
            "horas_solicitadas",
            "horas_aprovadas",
            "observacoes_secretaria",
            "referencia_decisao",
            "excepcional_autorizado",
            "justificativa_excepcional",
            "justificativa_sem_processo",
        ]
        widgets = {
            "periodo_realizacao": forms.TextInput(attrs={"placeholder": "Ex.: 10/03/2026 ou 10 a 12/03/2026"}),
            "descricao": forms.TextInput(attrs={"placeholder": "Descreva a atividade comprovada"}),
            "quantidade": forms.NumberInput(attrs={"step": "0.01", "min": "0.01"}),
            "horas_solicitadas": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
            "horas_aprovadas": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
            "observacoes_secretaria": forms.Textarea(attrs={"rows": 3}),
            "referencia_decisao": forms.Textarea(attrs={"rows": 2}),
            "justificativa_excepcional": forms.Textarea(attrs={"rows": 2}),
            "justificativa_sem_processo": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, aluno=None, processo=None, usuario=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.aluno = aluno
        self.processo = processo
        self.usuario = usuario
        trajetorias = TrajetoriaAcademica.objects.none()
        trajetoria = None
        if aluno:
            trajetorias = aluno.trajetorias.order_by("-criado_em")
            trajetoria_id = None
            if self.data:
                trajetoria_id = self.data.get(self.add_prefix("trajetoria"))
            if trajetoria_id:
                trajetoria = trajetorias.filter(pk=trajetoria_id).first()
            if not trajetoria:
                trajetoria = aluno.trajetoria_ativa() or trajetorias.first()
        self.trajetoria = trajetoria
        self.fields["trajetoria"].queryset = trajetorias
        if trajetoria:
            self.fields["trajetoria"].initial = trajetoria
        norma = LancamentoHorasComplementares.norma_para_trajetoria(trajetoria) if trajetoria else None
        self.norma = norma
        self.fields["tipo_atividade"].queryset = TipoAtividadeHorasComplementares.objects.none()
        normas_ids = []
        for item_trajetoria in trajetorias:
            item_norma = LancamentoHorasComplementares.norma_para_trajetoria(item_trajetoria)
            if item_norma:
                normas_ids.append(item_norma.id)
        if normas_ids:
            self.fields["tipo_atividade"].queryset = TipoAtividadeHorasComplementares.objects.filter(
                norma_id__in=normas_ids,
                ativo=True,
            ).select_related("grupo_limite", "norma")
        self.fields["tipo_atividade"].label_from_instance = (
            lambda obj: f"{obj.nome} ({obj.norma.get_nivel_curso_display()} - {obj.norma.identificacao})"
        )
        self.fields["processo_origem"].queryset = Processo.objects.none()
        if aluno:
            self.fields["processo_origem"].queryset = Processo.objects.filter(
                usuario_criado_por=aluno,
                tipo=Processo.TipoProcesso.HORAS_COMPLEMENTARES,
            ).order_by("-data_criacao")
        if processo:
            self.fields["processo_origem"].initial = processo
            self.fields["processo_origem"].widget = forms.HiddenInput()
        self.fields["retificar_lancamento"].queryset = LancamentoHorasComplementares.objects.none()
        if trajetoria:
            self.fields["retificar_lancamento"].queryset = LancamentoHorasComplementares.ativos().filter(
                trajetoria=trajetoria,
            )
        self.fields["retificar_lancamento"].required = False
        self.fields["justificativa_sem_processo"].required = False

    def clean(self):
        cleaned_data = super().clean()
        tipo = cleaned_data.get("tipo_atividade")
        trajetoria = cleaned_data.get("trajetoria")
        processo_origem = self.processo or cleaned_data.get("processo_origem")
        quantidade = cleaned_data.get("quantidade")
        horas_aprovadas = cleaned_data.get("horas_aprovadas")
        retificado = cleaned_data.get("retificar_lancamento")
        if not trajetoria:
            self.add_error("trajetoria", "Selecione a trajetória acadêmica.")
            return cleaned_data
        norma = LancamentoHorasComplementares.norma_para_trajetoria(trajetoria)
        if not norma:
            raise forms.ValidationError("Não há norma vigente de horas complementares para o nível do discente.")
        if not processo_origem and not (cleaned_data.get("justificativa_sem_processo") or "").strip():
            self.add_error("processo_origem", "Informe o processo de origem ou justifique o lançamento sem processo.")
        if processo_origem and processo_origem.usuario_criado_por_id != trajetoria.aluno_id:
            self.add_error("processo_origem", "O processo de origem deve pertencer ao discente da trajetória.")
        if tipo and tipo.norma_id != norma.id:
            self.add_error("tipo_atividade", "Selecione uma atividade da norma vigente da trajetória.")
        if tipo and quantidade:
            calculadas = quantidade * tipo.horas_por_unidade
            self.horas_calculadas = calculadas
            lancamento = LancamentoHorasComplementares(
                trajetoria=trajetoria,
                processo_origem=processo_origem,
                tipo_atividade=tipo,
                norma=tipo.norma,
                grupo_limite=tipo.grupo_limite,
                descricao=cleaned_data.get("descricao", ""),
                periodo_realizacao=cleaned_data.get("periodo_realizacao", ""),
                quantidade=quantidade,
                unidade_quantidade=tipo.unidade_calculo,
                horas_solicitadas=cleaned_data.get("horas_solicitadas") or calculadas,
                horas_calculadas=calculadas,
                horas_aprovadas=horas_aprovadas or 0,
                observacoes_secretaria=cleaned_data.get("observacoes_secretaria", ""),
                referencia_decisao=cleaned_data.get("referencia_decisao", ""),
                excepcional_autorizado=cleaned_data.get("excepcional_autorizado") or False,
                justificativa_excepcional=cleaned_data.get("justificativa_excepcional", ""),
                justificativa_sem_processo=cleaned_data.get("justificativa_sem_processo", ""),
                criado_por=self.usuario,
                substitui_lancamento=retificado,
            )
            maximo = lancamento.maximo_aprovavel()
            self.maximo_aprovavel = maximo
            if maximo is not None and horas_aprovadas is not None and horas_aprovadas > maximo:
                if not cleaned_data.get("excepcional_autorizado"):
                    self.add_error("horas_aprovadas", f"O máximo aprovável pelas regras vigentes e {maximo}h.")
                elif not (cleaned_data.get("justificativa_excepcional") or "").strip():
                    self.add_error("justificativa_excepcional", "Justifique a aprovação excepcional acima do limite.")
            if horas_aprovadas is not None and horas_aprovadas != calculadas:
                if not (cleaned_data.get("observacoes_secretaria") or "").strip():
                    self.add_error("observacoes_secretaria", "Justifique a diferenca entre horas calculadas e aprovadas.")
        return cleaned_data

    def save(self, commit=True):
        tipo = self.cleaned_data["tipo_atividade"]
        trajetoria = self.cleaned_data["trajetoria"]
        processo_origem = self.processo or self.cleaned_data.get("processo_origem")
        quantidade = self.cleaned_data["quantidade"]
        retificado = self.cleaned_data.get("retificar_lancamento")
        lancamento = super().save(commit=False)
        lancamento.trajetoria = trajetoria
        lancamento.processo_origem = processo_origem
        lancamento.tipo_atividade = tipo
        lancamento.norma = tipo.norma
        lancamento.grupo_limite = tipo.grupo_limite
        lancamento.unidade_quantidade = tipo.unidade_calculo
        lancamento.horas_calculadas = quantidade * tipo.horas_por_unidade
        if not lancamento.horas_solicitadas:
            lancamento.horas_solicitadas = lancamento.horas_calculadas
        lancamento.limite_individual_no_lancamento = tipo.limite_individual
        lancamento.limite_grupo_no_lancamento = tipo.grupo_limite.limite_maximo if tipo.grupo_limite else None
        lancamento.criado_por = self.usuario
        lancamento.substitui_lancamento = retificado
        if commit:
            with transaction.atomic():
                if retificado:
                    retificado.status = LancamentoHorasComplementares.Status.RETIFICADO
                    retificado.save(update_fields=["status", "atualizado_em"])
                lancamento.save()
        return lancamento


class HorasComplementaresAdministrativoForm(OpcoesVaziasNomeadas, forms.Form):
    tipo_atividade = forms.ModelChoiceField(
        queryset=TipoAtividadeHorasComplementares.objects.none(),
        label="Tipo da atividade",
    )
    horas_aprovadas = forms.DecimalField(
        label="Quantidade de horas",
        min_value=Decimal("0.01"),
        max_digits=6,
        decimal_places=2,
        widget=forms.NumberInput(attrs={"step": "0.01", "min": "0.01"}),
    )
    comentario = forms.CharField(
        label="Comentario",
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    excepcional_autorizado = forms.BooleanField(
        required=False,
        label="Autorizar excepcionalidade acima do limite normativo",
    )

    def __init__(self, *args, trajetoria=None, usuario=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.trajetoria = trajetoria
        self.usuario = usuario
        self.norma = LancamentoHorasComplementares.norma_para_trajetoria(trajetoria) if trajetoria else None
        if self.norma:
            self.fields["tipo_atividade"].queryset = self.norma.tipos_atividade.filter(
                ativo=True,
            ).select_related("grupo_limite", "norma")

    def clean(self):
        cleaned_data = super().clean()
        tipo = cleaned_data.get("tipo_atividade")
        horas = cleaned_data.get("horas_aprovadas")
        comentario = (cleaned_data.get("comentario") or "").strip()
        excepcional = cleaned_data.get("excepcional_autorizado") or False

        if not self.trajetoria:
            raise forms.ValidationError("Trajetória acadêmica não encontrada.")
        if self.trajetoria.nivel_curso == Aluno.NivelCurso.POSDOUTORADO:
            raise forms.ValidationError("Trajetórias de Pós-Doutorado não possuem horas complementares.")
        if not self.norma:
            raise forms.ValidationError("Não há norma vigente de horas complementares para esta trajetória.")
        if tipo and tipo.norma_id != self.norma.id:
            self.add_error("tipo_atividade", "Selecione uma atividade da norma vigente da trajetória.")
        if not (tipo and horas):
            return cleaned_data

        quantidade = (horas / tipo.horas_por_unidade).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        lancamento = LancamentoHorasComplementares(
            trajetoria=self.trajetoria,
            tipo_atividade=tipo,
            norma=tipo.norma,
            grupo_limite=tipo.grupo_limite,
            descricao=comentario[:255] or "Lançamento administrativo de horas complementares",
            quantidade=quantidade,
            unidade_quantidade=tipo.unidade_calculo,
            horas_solicitadas=horas,
            horas_calculadas=quantidade * tipo.horas_por_unidade,
            horas_aprovadas=horas,
            observacoes_secretaria=comentario,
            excepcional_autorizado=excepcional,
            justificativa_excepcional=comentario if excepcional else "",
            justificativa_sem_processo=comentario,
            criado_por=self.usuario,
        )
        maximo = lancamento.maximo_aprovavel()
        if maximo is not None and horas > maximo and not excepcional:
            self.add_error("horas_aprovadas", f"O máximo aprovável pelas regras vigentes e {maximo}h.")
        return cleaned_data

    def save(self):
        tipo = self.cleaned_data["tipo_atividade"]
        horas = self.cleaned_data["horas_aprovadas"]
        comentario = self.cleaned_data["comentario"].strip()
        excepcional = self.cleaned_data.get("excepcional_autorizado") or False
        quantidade = (horas / tipo.horas_por_unidade).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return LancamentoHorasComplementares.objects.create(
            trajetoria=self.trajetoria,
            tipo_atividade=tipo,
            norma=tipo.norma,
            grupo_limite=tipo.grupo_limite,
            descricao=comentario[:255] or "Lançamento administrativo de horas complementares",
            quantidade=quantidade,
            unidade_quantidade=tipo.unidade_calculo,
            horas_solicitadas=horas,
            horas_calculadas=quantidade * tipo.horas_por_unidade,
            horas_aprovadas=horas,
            observacoes_secretaria=comentario,
            excepcional_autorizado=excepcional,
            justificativa_excepcional=comentario if excepcional else "",
            justificativa_sem_processo=comentario,
            criado_por=self.usuario,
        )


class FinalizarProcessoForm(forms.Form):
    termo_finalizacao = forms.CharField(
        label="Termo de finalização",
        widget=forms.Textarea(attrs={"rows": 5}),
    )


class AlunoComentarioForm(OpcoesVaziasNomeadas, forms.Form):
    comentario = forms.CharField(
        label="Comentário da alteração",
        widget=forms.Textarea(attrs={"rows": 3}),
    )


class AlunoStatusForm(AlunoComentarioForm):
    status_aluno = forms.ChoiceField(choices=Aluno.StatusAluno.choices, label="Status do aluno")


class AlunoDadosForm(AlunoComentarioForm):
    nome = forms.CharField(max_length=255, label="Nome")
    email = forms.EmailField(label="Email")
    matricula = forms.CharField(max_length=50, required=False, label="Matrícula")
    cpf = forms.CharField(max_length=14, required=False, label="CPF")
    genero = forms.ChoiceField(
        choices=(("", "---------"), *Aluno.Genero.choices),
        required=False,
        label="Gênero",
    )
    sexo_atribuido_nascimento = forms.ChoiceField(
        choices=(("", "---------"), *Aluno.SexoAtribuidoNascimento.choices),
        required=False,
        label="Sexo atribuído ao nascer",
    )
    polo_atuacao = forms.ModelChoiceField(
        queryset=Polo.objects.none(),
        required=False,
        label="Polo",
    )

    def __init__(self, *args, aluno=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.aluno = aluno
        self.fields["polo_atuacao"].queryset = Polo.objects.filter(ativo=True).order_by("nome")

    def clean_email(self):
        email = self.cleaned_data["email"]
        queryset = User.objects.filter(email__iexact=email)
        if self.aluno:
            queryset = queryset.exclude(pk=self.aluno.pk)
        if queryset.exists():
            raise forms.ValidationError("Já existe um usuário com este email.")
        return email

    def clean_cpf(self):
        cpf = "".join(char for char in (self.cleaned_data.get("cpf") or "") if char.isdigit())
        if cpf and not validar_cpf_brasileiro(cpf):
            raise forms.ValidationError("Informe um CPF válido.")
        queryset = Aluno.objects.filter(cpf=cpf)
        if self.aluno:
            queryset = queryset.exclude(pk=self.aluno.pk)
        if cpf and queryset.exists():
            raise forms.ValidationError("Já existe um aluno com este CPF.")
        return cpf or None


class AlunoCpfForm(forms.Form):
    cpf = forms.CharField(max_length=14, label="CPF")

    def __init__(self, *args, aluno=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.aluno = aluno
        self.fields["cpf"].widget.attrs.update(
            {"inputmode": "numeric", "autocomplete": "off", "placeholder": "000.000.000-00"}
        )

    def clean_cpf(self):
        cpf = "".join(char for char in self.cleaned_data["cpf"] if char.isdigit())
        if not validar_cpf_brasileiro(cpf):
            raise forms.ValidationError("Informe um CPF válido.")
        if Aluno.objects.filter(cpf=cpf).exclude(pk=self.aluno.pk).exists():
            raise forms.ValidationError("Este CPF já está cadastrado.")
        return cpf


class AlunoCadastroForm(OpcoesVaziasNomeadas, forms.Form):
    POLOS_CADASTRO_ALUNO = ("POLI", "Caruaru", "Garanhuns", "Petrolina", "Fitec/SP")

    nome = forms.CharField(max_length=255, label="Nome completo")
    email = forms.EmailField(label="Email")
    cpf = forms.CharField(max_length=14, label="CPF")
    genero = forms.ChoiceField(
        choices=(("", "---------"), *Aluno.Genero.choices),
        required=False,
        label="Gênero",
    )
    password1 = forms.CharField(label="Senha", widget=forms.PasswordInput)
    password2 = forms.CharField(label="Confirmar senha", widget=forms.PasswordInput)
    polo_atuacao = forms.ModelChoiceField(
        queryset=Polo.objects.none(),
        label="Polo do aluno",
    )
    sexo_atribuido_nascimento = forms.ChoiceField(
        choices=(("", "---------"), *Aluno.SexoAtribuidoNascimento.choices),
        required=False,
        label="Sexo atribuído ao nascer",
    )
    nivel_curso = forms.ChoiceField(choices=Aluno.NivelCurso.choices, label="Tipo de curso")
    ingresso = forms.CharField(label="Ingresso (ano ou semestre)", max_length=6)
    orientador = forms.ModelChoiceField(
        queryset=User.objects.filter(tipo_usuario=User.TipoUsuario.DOCENTE, is_active=True).order_by("nome"),
        required=False,
        label="Orientador",
    )
    tipo_coorientador = forms.ChoiceField(
        choices=(
            ("NENHUM", "Sem coorientador"),
            ("CADASTRADO", "Docente cadastrado"),
            ("EXTERNO", "Coorientador externo"),
        ),
        label="Tipo de coorientador",
    )
    coorientador = forms.ModelChoiceField(
        queryset=User.objects.filter(tipo_usuario=User.TipoUsuario.DOCENTE, is_active=True).order_by("nome"),
        required=False,
        label="Coorientador cadastrado",
    )
    coorientador_externo_nome = forms.CharField(max_length=255, required=False, label="Nome do coorientador externo")
    coorientador_externo_email = forms.EmailField(required=False, label="Email do coorientador externo")
    coorientador_externo_instituicao = forms.CharField(
        max_length=255,
        required=False,
        label="Instituição do coorientador externo",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["polo_atuacao"].queryset = Polo.objects.filter(
            nome__in=self.POLOS_CADASTRO_ALUNO,
            ativo=True,
        ).order_by("nome")
        self.fields["cpf"].widget.attrs.update(
            {"inputmode": "numeric", "autocomplete": "off", "placeholder": "000.000.000-00"}
        )

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("Já existe um usuário com este email.")
        return email

    def clean_cpf(self):
        cpf = "".join(char for char in self.cleaned_data["cpf"] if char.isdigit())
        if not validar_cpf_brasileiro(cpf):
            raise forms.ValidationError("Informe um CPF válido.")
        if Aluno.objects.filter(cpf=cpf).exists():
            raise forms.ValidationError("Este CPF já está cadastrado.")
        return cpf

    def clean_ingresso(self):
        ingresso = self.cleaned_data["ingresso"].strip()
        if re.match(r"^\d{4}$", ingresso):
            return f"{ingresso}.1"
        if not re.match(r"^\d{4}\.[12]$", ingresso):
            raise forms.ValidationError("Informe o ano ou semestre no formato YYYY, YYYY.1 ou YYYY.2.")
        return ingresso

    def clean(self):
        cleaned_data = super().clean()
        password1 = cleaned_data.get("password1")
        password2 = cleaned_data.get("password2")
        nivel_curso = cleaned_data.get("nivel_curso")
        tipo_coorientador = cleaned_data.get("tipo_coorientador")
        coorientador = cleaned_data.get("coorientador")
        externo_nome = (cleaned_data.get("coorientador_externo_nome") or "").strip()

        if password1 and password2 and password1 != password2:
            self.add_error("password2", "As senhas não conferem.")
        if password1:
            try:
                password_validation.validate_password(password1)
            except ValidationError as exc:
                self.add_error("password1", exc)

        usa_orientacao = nivel_curso in {
            Aluno.NivelCurso.MESTRADO,
            Aluno.NivelCurso.DOUTORADO,
        }
        usa_supervisao = nivel_curso == Aluno.NivelCurso.POSDOUTORADO
        if not usa_orientacao:
            if not usa_supervisao:
                cleaned_data["orientador"] = None
            cleaned_data["tipo_coorientador"] = "NENHUM"
            cleaned_data["coorientador"] = None
            cleaned_data["coorientador_externo_nome"] = ""
            cleaned_data["coorientador_externo_email"] = ""
            cleaned_data["coorientador_externo_instituicao"] = ""

        if usa_supervisao and not cleaned_data.get("orientador"):
            self.add_error("orientador", "Selecione o supervisor do Pós-Doutorado.")
        elif tipo_coorientador == "CADASTRADO" and not coorientador:
            self.add_error("coorientador", "Selecione um docente cadastrado.")
        elif tipo_coorientador == "EXTERNO" and not externo_nome:
            self.add_error("coorientador_externo_nome", "Informe o nome do coorientador externo.")

        return cleaned_data

    def save(self):
        dados = self.cleaned_data
        aluno = Aluno.objects.create_user(
            email=dados["email"],
            password=dados["password1"],
            nome=dados["nome"],
            cpf=dados["cpf"],
            genero=dados["genero"],
            status_aluno=Aluno.StatusAluno.EM_AVALIACAO,
            polo_atuacao=dados["polo_atuacao"],
            sexo_atribuido_nascimento=dados.get("sexo_atribuido_nascimento") or "",
        )
        trajetoria = TrajetoriaAcademica(
            aluno=aluno,
            nivel_curso=dados["nivel_curso"],
            status=TrajetoriaAcademica.Status.EM_HOMOLOGACAO,
            ingresso=dados["ingresso"],
            orientador=dados["orientador"],
        )
        if dados["tipo_coorientador"] == "CADASTRADO":
            trajetoria.coorientador = dados["coorientador"]
        elif dados["tipo_coorientador"] == "EXTERNO":
            trajetoria.coorientador_externo_nome = dados["coorientador_externo_nome"]
            trajetoria.coorientador_externo_email = dados["coorientador_externo_email"]
            trajetoria.coorientador_externo_instituicao = dados["coorientador_externo_instituicao"]
        trajetoria.save()
        return aluno


class ImportacaoIngressantesForm(forms.Form):
    arquivo = forms.FileField(
        label="Planilha de ingressantes",
        widget=forms.FileInput(attrs={"accept": ".csv,.xls,.xlsx"}),
        help_text="Arquivo CSV, XLS ou XLSX com as colunas nome, cpf, e-mail e orientador.",
    )
    nivel_curso = forms.ChoiceField(
        label="Ingresso do aluno",
        choices=(
            (Aluno.NivelCurso.ALUNO_ESPECIAL, "Aluno especial"),
            (Aluno.NivelCurso.MESTRADO, "Mestrado"),
            (Aluno.NivelCurso.DOUTORADO, "Doutorado"),
        ),
    )
    ingresso = forms.CharField(
        label="Semestre de ingresso",
        max_length=6,
        widget=forms.TextInput(attrs={"placeholder": "2026.2"}),
    )

    def clean_arquivo(self):
        arquivo = self.cleaned_data["arquivo"]
        extensao = Path(arquivo.name).suffix.lower()
        if extensao not in {".csv", ".xls", ".xlsx"}:
            raise forms.ValidationError("Envie um arquivo CSV, XLS ou XLSX.")
        if arquivo.size > 5 * 1024 * 1024:
            raise forms.ValidationError("O arquivo deve ter no máximo 5 MB.")
        return arquivo

    def clean_ingresso(self):
        ingresso = self.cleaned_data["ingresso"].strip()
        if not re.fullmatch(r"\d{4}\.[12]", ingresso):
            raise forms.ValidationError("Informe o semestre no formato YYYY.1 ou YYYY.2.")
        return ingresso


class AlunoQualificacaoForm(AlunoComentarioForm):
    isQualificado = forms.BooleanField(required=False, label="Aluno qualificado")


class AlunoPrazoForm(AlunoComentarioForm):
    valor_semestre = forms.CharField(
        label="Semestre (YYYY.1 ou YYYY.2)",
        max_length=6,
    )


class AlunoReingressoForm(AlunoComentarioForm):
    ingresso = forms.CharField(
        label="Novo ingresso (YYYY.1 ou YYYY.2)",
        max_length=6,
    )
    prazo_qualificacao = forms.CharField(
        label="Novo prazo",
        max_length=6,
    )
    prazo_defesa = forms.CharField(
        label="Novo prazo de defesa",
        max_length=6,
    )


class AlunoIniciarDoutoradoForm(AlunoComentarioForm):
    ingresso = forms.CharField(
        label="Ingresso no doutorado (YYYY.1 ou YYYY.2)",
        max_length=6,
    )
    prazo_qualificacao = forms.CharField(
        label="Prazo de qualificação (YYYY.1 ou YYYY.2)",
        max_length=6,
    )
    prazo_defesa = forms.CharField(
        label="Prazo de defesa (YYYY.1 ou YYYY.2)",
        max_length=6,
    )
    orientador = forms.ModelChoiceField(
        queryset=User.objects.filter(tipo_usuario=User.TipoUsuario.DOCENTE).order_by("nome"),
        required=False,
        label="Orientador do doutorado",
    )


class AlunoDefesaForm(AlunoComentarioForm):
    numero_defesa = forms.CharField(label="Número da defesa", max_length=80)
    data_defesa = forms.DateField(
        label="Data da defesa",
        widget=forms.DateInput(attrs={"type": "date"}),
    )


class AlunoDepositoFinalForm(AlunoComentarioForm):
    deposito_versao_final = forms.BooleanField(required=False, label="Depósito da versão final")


class AlunoOrientadorForm(AlunoComentarioForm):
    orientador = forms.ModelChoiceField(
        queryset=User.objects.filter(tipo_usuario=User.TipoUsuario.DOCENTE).order_by("nome"),
        required=False,
        label="Orientador",
    )


class AlunoCoorientadorForm(AlunoComentarioForm):
    class TipoCoorientador:
        NENHUM = "NENHUM"
        CADASTRADO = "CADASTRADO"
        EXTERNO = "EXTERNO"

        choices = (
            (NENHUM, "Sem coorientador"),
            (CADASTRADO, "Docente cadastrado"),
            (EXTERNO, "Coorientador externo"),
        )

    tipo_coorientador = forms.ChoiceField(
        choices=TipoCoorientador.choices,
        label="Tipo de coorientador",
    )
    coorientador = forms.ModelChoiceField(
        queryset=User.objects.filter(tipo_usuario=User.TipoUsuario.DOCENTE).order_by("nome"),
        required=False,
        label="Coorientador cadastrado",
    )
    coorientador_externo_nome = forms.CharField(
        max_length=255,
        required=False,
        label="Nome do coorientador externo",
    )
    coorientador_externo_email = forms.EmailField(
        required=False,
        label="Email do coorientador externo",
    )
    coorientador_externo_instituicao = forms.CharField(
        max_length=255,
        required=False,
        label="Instituição do coorientador externo",
    )

    def clean(self):
        cleaned_data = super().clean()
        tipo_coorientador = cleaned_data.get("tipo_coorientador")
        coorientador = cleaned_data.get("coorientador")
        externo_nome = (cleaned_data.get("coorientador_externo_nome") or "").strip()

        if tipo_coorientador == self.TipoCoorientador.CADASTRADO and not coorientador:
            self.add_error("coorientador", "Selecione um docente cadastrado.")

        if tipo_coorientador == self.TipoCoorientador.EXTERNO and not externo_nome:
            self.add_error("coorientador_externo_nome", "Informe o nome do coorientador externo.")

        return cleaned_data


class TrajetoriaAcademicaForm(AlunoComentarioForm):
    class TipoCoorientador:
        NENHUM = "NENHUM"
        CADASTRADO = "CADASTRADO"
        EXTERNO = "EXTERNO"

        choices = (
            (NENHUM, "Sem coorientador"),
            (CADASTRADO, "Docente cadastrado"),
            (EXTERNO, "Coorientador externo"),
        )

    trajetoria_id = forms.IntegerField(required=False, widget=forms.HiddenInput)
    nivel_curso = forms.ChoiceField(choices=Aluno.NivelCurso.choices, label="Nivel")
    status = forms.ChoiceField(choices=TrajetoriaAcademica.Status.choices, label="Status da trajetória")
    ingresso = forms.CharField(label="Ingresso (YYYY.1 ou YYYY.2)", max_length=6)
    prazo_qualificacao = forms.CharField(label="Prazo", max_length=6, required=False)
    prazo_defesa = forms.CharField(label="Prazo de defesa", max_length=6, required=False)
    reingressante = forms.BooleanField(required=False, label="Reingressante")
    isQualificado = forms.BooleanField(required=False, label="Projeto/qualificação concluído")
    orientador = forms.ModelChoiceField(
        queryset=User.objects.filter(tipo_usuario=User.TipoUsuario.DOCENTE).order_by("nome"),
        required=False,
        label="Orientador",
    )
    tipo_coorientador = forms.ChoiceField(choices=TipoCoorientador.choices, label="Tipo de coorientador")
    coorientador = forms.ModelChoiceField(
        queryset=User.objects.filter(tipo_usuario=User.TipoUsuario.DOCENTE).order_by("nome"),
        required=False,
        label="Coorientador cadastrado",
    )
    coorientador_externo_nome = forms.CharField(max_length=255, required=False, label="Nome do coorientador externo")
    coorientador_externo_email = forms.EmailField(required=False, label="Email do coorientador externo")
    coorientador_externo_instituicao = forms.CharField(
        max_length=255,
        required=False,
        label="Instituição do coorientador externo",
    )
    numero_defesa = forms.CharField(max_length=80, required=False, label="Número da defesa")
    data_defesa = forms.DateField(required=False, label="Data da defesa", widget=forms.DateInput(attrs={"type": "date"}))
    deposito_versao_final = forms.BooleanField(required=False, label="Depósito da versão final")

    def clean(self):
        cleaned_data = super().clean()
        tipo_coorientador = cleaned_data.get("tipo_coorientador")
        coorientador = cleaned_data.get("coorientador")
        externo_nome = (cleaned_data.get("coorientador_externo_nome") or "").strip()
        nivel_curso = cleaned_data.get("nivel_curso")
        status = cleaned_data.get("status")
        numero_defesa = (cleaned_data.get("numero_defesa") or "").strip()
        data_defesa = cleaned_data.get("data_defesa")
        usa_orientacao = nivel_curso in {
            Aluno.NivelCurso.MESTRADO,
            Aluno.NivelCurso.DOUTORADO,
        }
        usa_supervisao = nivel_curso == Aluno.NivelCurso.POSDOUTORADO
        usa_conclusao = nivel_curso in {
            Aluno.NivelCurso.MESTRADO,
            Aluno.NivelCurso.DOUTORADO,
            Aluno.NivelCurso.POSDOUTORADO,
        }
        conclusao_label = "relatório final" if nivel_curso == Aluno.NivelCurso.POSDOUTORADO else "defesa"

        if not usa_orientacao:
            cleaned_data["prazo_qualificacao"] = ""
            cleaned_data["prazo_defesa"] = ""
            cleaned_data["reingressante"] = False
            cleaned_data["isQualificado"] = False
            if not usa_supervisao:
                cleaned_data["orientador"] = None
            cleaned_data["tipo_coorientador"] = self.TipoCoorientador.NENHUM
            cleaned_data["coorientador"] = None
            cleaned_data["coorientador_externo_nome"] = ""
            cleaned_data["coorientador_externo_email"] = ""
            cleaned_data["coorientador_externo_instituicao"] = ""

        if usa_supervisao and not cleaned_data.get("orientador"):
            self.add_error("orientador", "Selecione o supervisor do Pós-Doutorado.")

        if not usa_conclusao:
            cleaned_data["numero_defesa"] = ""
            cleaned_data["data_defesa"] = None
            cleaned_data["deposito_versao_final"] = False
        elif nivel_curso == Aluno.NivelCurso.POSDOUTORADO:
            cleaned_data["deposito_versao_final"] = False

        if usa_orientacao and tipo_coorientador == self.TipoCoorientador.CADASTRADO and not coorientador:
            self.add_error("coorientador", "Selecione um docente cadastrado.")
        if usa_orientacao and tipo_coorientador == self.TipoCoorientador.EXTERNO and not externo_nome:
            self.add_error("coorientador_externo_nome", "Informe o nome do coorientador externo.")
        if status == TrajetoriaAcademica.Status.CONCLUIDA and usa_conclusao:
            if not numero_defesa:
                self.add_error("numero_defesa", f"Informe o número do {conclusao_label}.")
            if not data_defesa:
                self.add_error("data_defesa", f"Informe a data do {conclusao_label}.")

        return cleaned_data


class TrajetoriaStatusForm(AlunoComentarioForm):
    status = forms.ChoiceField(
        choices=(
            (TrajetoriaAcademica.Status.ATIVA, "Ativo"),
            (TrajetoriaAcademica.Status.DESLIGADA, "Desligado"),
            (TrajetoriaAcademica.Status.TRANCADA, "Trancado"),
        ),
        label="Status",
    )


class NovoEstagioDocenciaForm(AlunoComentarioForm):
    trajetoria_id = forms.IntegerField(widget=forms.HiddenInput())
    supervisor = forms.CharField(max_length=255, label="Supervisor", required=True)
    status = forms.ChoiceField(choices=EstagioDocencia.Status.choices, required=True, label="Em Andamento")
    
    # Removendo o 'required=False', eles se tornam obrigatórios automaticamente
    inicio = forms.DateField(label="Data Início", widget=forms.DateInput(attrs={"type": "date"}))
    termino = forms.DateField(label="Data Término", widget=forms.DateInput(attrs={"type": "date"}))


class EstagioDocenciaUpdateForm(AlunoComentarioForm):
    estagio_id = forms.IntegerField(widget=forms.HiddenInput())
    supervisor = forms.CharField(max_length=255, required=False, label="Nome do Supervisor")
    status = forms.ChoiceField(choices=EstagioDocencia.Status.choices, label="Status")
    inicio = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    termino = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))

    def clean(self):
        cleaned_data = super().clean()
        data_inicio = cleaned_data.get("inicio")
        data_termino = cleaned_data.get("termino")

        # Validação lógica: data de término não pode ser anterior ao início
        if data_inicio and data_termino:
            if data_termino < data_inicio:
                self.add_error(
                    "termino", 
                    "A data de término não pode ser anterior à data de início."
                )

        return cleaned_data
    
