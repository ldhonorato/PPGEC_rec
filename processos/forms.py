import re
from datetime import datetime
from pathlib import Path

from django import forms
from django.contrib.auth import password_validation
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils import timezone

from .models import (
    Aluno,
    DisponibilidadeSala,
    DisciplinaTrajetoria,
    Documento,
    EstagioDocencia,
    MembroBanca,
    Polo,
    PublicacaoTrajetoria,
    Processo,
    ReservaAmbiente,
    Sala,
    SolicitacaoBanca,
    Setor,
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


class DisponibilidadeSalaForm(forms.ModelForm):
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
        label="Hora de inicio",
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
            self.add_error("hora_fim", "O horario final deve ser posterior ao horario inicial.")
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


class SolicitacaoBancaForm(forms.ModelForm):
    aluno = forms.ModelChoiceField(queryset=Aluno.objects.none(), label="Aluno")
    trajetoria = forms.ModelChoiceField(queryset=TrajetoriaAcademica.objects.none(), label="Trajetoria academica")

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
                label=f"{label} - Instituicao/IES",
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
            self.add_error("trajetoria", "A trajetoria selecionada nao pertence ao aluno.")
        if trajetoria and self.docente:
            docente_vinculado = trajetoria.orientador_id == self.docente.id or trajetoria.coorientador_id == self.docente.id
            if not docente_vinculado:
                self.add_error("trajetoria", "Selecione uma trajetoria sob sua orientacao ou coorientacao.")
        if trajetoria and tipo_defesa:
            if tipo_defesa == SolicitacaoBanca.TipoDefesa.DEFESA_MESTRADO and trajetoria.nivel_curso != Aluno.NivelCurso.MESTRADO:
                self.add_error("tipo_defesa", "Defesa de Mestrado exige trajetoria de mestrado.")
            if tipo_defesa in {
                SolicitacaoBanca.TipoDefesa.QUALIFICACAO_DOUTORADO,
                SolicitacaoBanca.TipoDefesa.DEFESA_DOUTORADO,
            } and trajetoria.nivel_curso != Aluno.NivelCurso.DOUTORADO:
                self.add_error("tipo_defesa", "Solicitacao de doutorado exige trajetoria de doutorado.")

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
                self.add_error(field_name, "Campo obrigatorio para finalizar.")
        if not cleaned_data.get("requisitos_cumpridos"):
            self.add_error("requisitos_cumpridos", "Confirme que o discente cumpre os requisitos.")
        if not cleaned_data.get("ciencia_recomendacao_mpf"):
            self.add_error("ciencia_recomendacao_mpf", "Confirme a ciencia para finalizar.")

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
                self.add_error(f"{prefixo}_instituicao", "Informe a instituicao/IES.")
            if MembroBanca.exige_cpf(tipo_defesa, papel) and not cpf:
                self.add_error(f"{prefixo}_cpf", "Informe o CPF.")
            elif cpf and not validar_cpf_brasileiro(cpf):
                self.add_error(f"{prefixo}_cpf", "Informe um CPF valido.")

    def _validar_cpfs_informados(self, cleaned_data):
        for papel, _label in MembroBanca.Papel.choices:
            cpf = (cleaned_data.get(f"membro_{papel}_cpf") or "").strip()
            if cpf and not validar_cpf_brasileiro(cpf):
                self.add_error(f"membro_{papel}_cpf", "Informe um CPF valido.")

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


class ReservaAmbienteForm(forms.Form):
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
    titulo = forms.CharField(max_length=255, required=False, label="Titulo")
    data_inicio = forms.DateField(
        label="Data de inicio",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    hora_inicio = forms.TimeField(
        label="Hora de inicio",
        widget=forms.TimeInput(attrs={"type": "time"}),
    )
    hora_fim = forms.TimeField(
        label="Hora de fim",
        widget=forms.TimeInput(attrs={"type": "time"}),
    )
    recorrencia = forms.ChoiceField(
        choices=(
            (RECORRENCIA_NENHUMA, "Nao repetir"),
            (RECORRENCIA_DIARIA, "Diaria"),
            (RECORRENCIA_SEMANAL, "Semanal"),
            (RECORRENCIA_MENSAL, "Mensal"),
        ),
        label="Recorrencia",
    )
    duracao_recorrencia_meses = forms.IntegerField(
        required=False,
        min_value=1,
        max_value=6,
        label="Duracao da recorrencia em meses",
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
            self.add_error("hora_fim", "A hora de fim deve ser posterior a hora de inicio no mesmo dia.")
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
    titulo = forms.CharField(max_length=255, label="Titulo")
    tipo_documento = forms.ChoiceField(
        choices=[("", "Selecione")] + list(Documento.TipoDocumento.choices),
        required=False,
        label="Tipo de documento",
    )
    arquivo = forms.FileField(
        required=True,
        label="Arquivo do documento",
        help_text="PDF, Office ou imagem. Tamanho maximo: 5 MB.",
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
            raise forms.ValidationError("O arquivo deve ter no maximo 5 MB.")

        extensao = Path(arquivo.name).suffix.lower()
        if extensao not in ALLOWED_DOCUMENTO_EXTENSIONS:
            raise forms.ValidationError(
                "Formato nao permitido. Envie PDF, arquivos Office ou imagens."
            )

        return arquivo


class EncaminhamentoForm(forms.Form):
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
    class Meta:
        model = Processo
        fields = ["tipo", "assunto", "descricao"]
        widgets = {
            "descricao": forms.Textarea(attrs={"rows": 5}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user


class SolicitarCienteOrientadorForm(forms.Form):
    mensagem_solicitacao = forms.CharField(
        required=False,
        label="Observacao da solicitacao",
        widget=forms.Textarea(attrs={"rows": 3}),
    )


class ManifestarCienteOrientadorForm(forms.Form):
    mensagem_manifestacao = forms.CharField(
        required=False,
        label="Mensagem da manifestacao",
        widget=forms.Textarea(attrs={"rows": 3}),
    )


class ComentarioProcessoForm(forms.Form):
    anonimo = forms.BooleanField(required=False, label="Comentario anonimo")
    texto = forms.CharField(
        label="Comentario",
        widget=forms.Textarea(attrs={"rows": 4}),
    )


class FinalizarProcessoForm(forms.Form):
    termo_finalizacao = forms.CharField(
        label="Termo de finalizacao",
        widget=forms.Textarea(attrs={"rows": 5}),
    )


class AlunoComentarioForm(forms.Form):
    comentario = forms.CharField(
        label="Comentario da alteracao",
        widget=forms.Textarea(attrs={"rows": 3}),
    )


class AlunoStatusForm(AlunoComentarioForm):
    status_aluno = forms.ChoiceField(choices=Aluno.StatusAluno.choices, label="Status do aluno")


class AlunoDadosForm(AlunoComentarioForm):
    nome = forms.CharField(max_length=255, label="Nome")
    email = forms.EmailField(label="Email")
    matricula = forms.CharField(max_length=50, required=False, label="Matricula")

    def __init__(self, *args, aluno=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.aluno = aluno

    def clean_email(self):
        email = self.cleaned_data["email"]
        queryset = User.objects.filter(email__iexact=email)
        if self.aluno:
            queryset = queryset.exclude(pk=self.aluno.pk)
        if queryset.exists():
            raise forms.ValidationError("Ja existe um usuario com este email.")
        return email


class AlunoCadastroForm(forms.Form):
    nome = forms.CharField(max_length=255, label="Nome completo")
    email = forms.EmailField(label="Email")
    password1 = forms.CharField(label="Senha", widget=forms.PasswordInput)
    password2 = forms.CharField(label="Confirmar senha", widget=forms.PasswordInput)
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
        label="Instituicao do coorientador externo",
    )

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("Ja existe um usuario com este email.")
        return email

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
            self.add_error("password2", "As senhas nao conferem.")
        if password1:
            try:
                password_validation.validate_password(password1)
            except ValidationError as exc:
                self.add_error("password1", exc)

        usa_orientacao = nivel_curso in {
            Aluno.NivelCurso.MESTRADO,
            Aluno.NivelCurso.DOUTORADO,
        }
        if not usa_orientacao:
            cleaned_data["orientador"] = None
            cleaned_data["tipo_coorientador"] = "NENHUM"
            cleaned_data["coorientador"] = None
            cleaned_data["coorientador_externo_nome"] = ""
            cleaned_data["coorientador_externo_email"] = ""
            cleaned_data["coorientador_externo_instituicao"] = ""
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
            status_aluno=Aluno.StatusAluno.EM_AVALIACAO,
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
        label="Prazo de qualificacao (YYYY.1 ou YYYY.2)",
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
    numero_defesa = forms.CharField(label="Numero da defesa", max_length=80)
    data_defesa = forms.DateField(
        label="Data da defesa",
        widget=forms.DateInput(attrs={"type": "date"}),
    )


class AlunoDepositoFinalForm(AlunoComentarioForm):
    deposito_versao_final = forms.BooleanField(required=False, label="Deposito da versao final")


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
        label="Instituicao do coorientador externo",
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
    status = forms.ChoiceField(choices=TrajetoriaAcademica.Status.choices, label="Status da trajetoria")
    ingresso = forms.CharField(label="Ingresso (YYYY.1 ou YYYY.2)", max_length=6)
    prazo_qualificacao = forms.CharField(label="Prazo", max_length=6, required=False)
    prazo_defesa = forms.CharField(label="Prazo de defesa", max_length=6, required=False)
    reingressante = forms.BooleanField(required=False, label="Reingressante")
    isQualificado = forms.BooleanField(required=False, label="Projeto/qualificacao concluido")
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
        label="Instituicao do coorientador externo",
    )
    numero_defesa = forms.CharField(max_length=80, required=False, label="Numero da defesa")
    data_defesa = forms.DateField(required=False, label="Data da defesa", widget=forms.DateInput(attrs={"type": "date"}))
    deposito_versao_final = forms.BooleanField(required=False, label="Deposito da versao final")

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
        usa_conclusao = nivel_curso in {
            Aluno.NivelCurso.MESTRADO,
            Aluno.NivelCurso.DOUTORADO,
            Aluno.NivelCurso.POSDOUTORADO,
        }
        conclusao_label = "relatorio final" if nivel_curso == Aluno.NivelCurso.POSDOUTORADO else "defesa"

        if not usa_orientacao:
            cleaned_data["prazo_qualificacao"] = ""
            cleaned_data["prazo_defesa"] = ""
            cleaned_data["reingressante"] = False
            cleaned_data["isQualificado"] = False
            cleaned_data["orientador"] = None
            cleaned_data["tipo_coorientador"] = self.TipoCoorientador.NENHUM
            cleaned_data["coorientador"] = None
            cleaned_data["coorientador_externo_nome"] = ""
            cleaned_data["coorientador_externo_email"] = ""
            cleaned_data["coorientador_externo_instituicao"] = ""

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
                self.add_error("numero_defesa", f"Informe o numero do {conclusao_label}.")
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
    