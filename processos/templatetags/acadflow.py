"""Filtros de apresentacao compartilhados pelos templates."""

from django import template
from django.urls import reverse

register = template.Library()

# Tom do badge por status de processo. O status sozinho, em texto puro, obriga
# a ler para saber se e bom, ruim ou neutro; a cor resolve de relance.
#
# A cor nao carrega a informacao sozinha -- o rotulo continua escrito ao lado,
# entao quem nao distingue as cores nao perde nada.
TONS_STATUS_PROCESSO = {
    "FINALIZADO": "ok",
    "EM_ANALISE": "info",
    "EM_DEBATE": "info",
    "AGUARDANDO_DOCUMENTO": "aviso",
    "AGUARDANDO_CIENCIA": "aviso",
}


@register.filter
def status_processo_tom(status):
    """Devolve o sufixo da classe de badge para um status de processo.

    Uso: <span class="badge badge-{{ processo.status|status_processo_tom }}">

    Status desconhecido cai em "info" em vez de quebrar: e o caso de registros
    antigos cujo valor saiu das choices atuais.
    """
    return TONS_STATUS_PROCESSO.get(status, "info")


@register.filter
def primeiro_nome(nome_completo):
    """Primeiro nome de uma pessoa, para saudacao.

    truncatewords:1 nao serve aqui: ele acrescenta reticencias, e a saudacao
    saia como "Ola, Aluno ...".
    """
    if not nome_completo:
        return ""
    return str(nome_completo).strip().split()[0]


@register.filter
def url_protegida(arquivo):
    """Endereco de um arquivo enviado, passando pela verificacao de permissao.

    Os templates usavam ``documento.arquivo.url`` e isso funcionava por
    coincidencia: com armazenamento em disco, ``.url`` produz "/media/<caminho>",
    que e a rota da view que confere a regra do documento.

    Com o S3 a coincidencia acaba. ``.url`` passa a devolver o endereco assinado
    do bucket, que abre o arquivo sem passar por lugar nenhum do sistema -- e o
    link, uma vez na tela, vale para quem o pegar.

    Este filtro devolve sempre a rota interna, seja qual for o armazenamento.
    Quem confere a permissao e decide como entregar e a view.
    """
    if not arquivo:
        return ""
    return reverse("media_file", kwargs={"path": arquivo.name})
