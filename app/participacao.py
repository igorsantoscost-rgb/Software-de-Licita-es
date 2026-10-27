"""
Participação de ME/EPP numa licitação (LC 123/2006): exclusiva, mista ou ampla.

- exclusiva: todos os itens são de participação exclusiva para ME/EPP;
- mista: parte exclusiva/cota reservada e parte em ampla concorrência;
- ampla: nenhum item exclusivo.

Fonte preferida: o benefício de cada item no PNCP (tipoBeneficioId). Sem isso,
procura as expressões no começo do edital/TR. Sem nada: None (desconhecido).
"""
import re
import unicodedata

# PNCP, tipoBeneficioId: 1 exclusiva ME/EPP, 2 subcontratação, 3 cota reservada,
# 4 sem benefício, 5 não se aplica
BENEFICIO_PNCP = {1: "exclusivo", 3: "cota", 2: "ampla", 4: "ampla", 5: "ampla"}

ROTULOS = {"exclusiva": "Exclusiva ME/EPP", "mista": "Mista (parte exclusiva ME/EPP)", "ampla": "Ampla concorrência"}


def _norm(txt):
    txt = unicodedata.normalize("NFKD", str(txt or ""))
    txt = "".join(c for c in txt if not unicodedata.combining(c)).lower()
    return re.sub(r"\s+", " ", txt)


_ME = r"(?:me\s*/\s*epp|me\s*,?\s*e\s*epp|mes?\s*/\s*epps?|microempresas?|empresas?\s+de\s+pequeno\s+porte|micro\s*empresas?)"
RE_EXCLUSIVA = re.compile(r"exclusiv[ao]s?\b[^.;:\n]{0,60}?" + _ME)
RE_EXCLUSIVA_2 = re.compile(_ME + r"[^.;:\n]{0,40}?exclusiv")
RE_NAO_EXCLUSIVA = re.compile(r"exclusiv\w*[^:\n]{0,40}:\s*(?:nao|n\.?a\.?o)\b|nao\s+(?:sera|e|havera)\s+(?:de\s+)?(?:participacao\s+)?exclusiv")
RE_COTA = re.compile(r"cota\s+(?:reservada|principal|de\s+ate\s+25|exclusiva)")
RE_AMPLA = re.compile(r"ampla\s+(?:participacao|concorrencia|disputa)")
RE_LEI_GENERICA = re.compile(r"art(?:igo)?\.?\s*48")


def pelos_itens(itens):
    """Pela marcação de benefício dos itens (PNCP). None se nenhum item tem."""
    marcas = [i.get("beneficio") for i in itens or [] if i.get("beneficio")]
    if not marcas:
        return None
    exclusivos = sum(1 for m in marcas if m == "exclusivo")
    especiais = sum(1 for m in marcas if m in ("exclusivo", "cota"))
    if exclusivos == len(marcas):
        return "exclusiva"
    if especiais:
        return "mista"
    return "ampla"


def pelo_texto(*textos):
    """Pelas expressões do edital/TR/objeto. None se não der para saber."""
    t = _norm(" ".join(x for x in textos if x))
    if not t.strip():
        return None
    if RE_NAO_EXCLUSIVA.search(t):
        return "mista" if RE_COTA.search(t) else "ampla"
    tem_cota = bool(RE_COTA.search(t))
    exclusiva = bool(RE_EXCLUSIVA.search(t) or RE_EXCLUSIVA_2.search(t))
    tem_ampla = bool(RE_AMPLA.search(t))
    if tem_cota or (exclusiva and tem_ampla):
        return "mista"
    if exclusiva:
        return "exclusiva"
    if tem_ampla:
        return "ampla"
    return None


def classificar(itens, *textos):
    """Devolve (situacao, fonte): situacao em exclusiva|mista|ampla|None."""
    pelos = pelos_itens(itens)
    if pelos:
        return pelos, "itens do PNCP"
    texto = pelo_texto(*textos)
    if texto:
        return texto, "edital"
    return None, None
