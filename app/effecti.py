"""
Portais que a Effecti acompanha (etapa "Monitorar").
Fonte: https://ajuda.effecti.com.br/ajuda-2/portais-disponiveis/ (conferido em 30/10/2026).

Licitacao em portal fora desta lista: o assessor acompanha na mao e o sistema
mostra o selo "Acompanhamento manual" onde a licitacao aparece.
Para incluir/tirar um portal, edite PORTAIS_EFFECTI (nome, padrao). O padrao e
procurado no nome do portal ja sem acento e em minusculas.
"""
import re

from app.capag import normalizar

PORTAIS_EFFECTI = [
    ("BLL", r"\bbll\b"),
    ("BNC", r"\bbnc\b|bolsa nacional de compras"),
    ("Compras BR", r"compras ?br\b"),
    ("Portal Amazonas", r"amazonas|e-?compras ?am\b"),
    ("Compras MT", r"compras ?mt\b"),
    ("Compras MG", r"compras ?\.?mg\b|compras\.mg\.gov"),
    ("Portal de Compras Publicas", r"\bpcp\b|compras ?publicas|portaldecompraspublicas"),
    ("Compras SC", r"compras ?sc\b"),
    ("ComprasNet", r"comprasnet|compras ?\.?gov|comprasgov"),
    ("Compras GO (antigo)", r"compras ?go\b|comprasnet ?\.?go"),
    ("Licitacoes-e (Banco do Brasil)", r"licitacoes ?-? ?e\b|licitacoes-e|banco do brasil"),
    ("Licitanet", r"licitanet"),
    ("Licitar Digital", r"licitar ?digital"),
    ("PE Integrado", r"pe ?integrado"),
    ("Publinexo", r"publinexo"),
    ("Siga RJ", r"siga ?\.?rj\b|\bsiga\b"),
    ("BBMNet", r"bbm ?net"),
]

_PADROES = [(nome, re.compile(p)) for nome, p in PORTAIS_EFFECTI]


def portal_effecti(portal):
    """Nome do portal na lista da Effecti que casa com o informado (ou None)."""
    n = normalizar(portal)
    if not n:
        return None
    for nome, padrao in _PADROES:
        if padrao.search(n):
            return nome
    return None


def acompanhamento_manual(portal):
    """True quando o portal esta preenchido e a Effecti nao acompanha.
    Portal vazio nao marca (o assessor ainda vai preencher)."""
    if not (portal or "").strip():
        return False
    return portal_effecti(portal) is None
