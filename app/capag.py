"""Consulta da Capacidade de Pagamento (CAPAG) do Tesouro Nacional.

A CAPAG e uma nota (A+, A, B+, B, C, D) que o Tesouro Nacional publica para
estados e municipios. Os dados sao importados para o banco local
(ver capag_import.py) e consultados aqui, sem depender de internet a cada uso.
"""
import unicodedata

# 26 estados + Distrito Federal
UFS = [
    ("AC", "Acre"), ("AL", "Alagoas"), ("AP", "Amapa"), ("AM", "Amazonas"),
    ("BA", "Bahia"), ("CE", "Ceara"), ("DF", "Distrito Federal"),
    ("ES", "Espirito Santo"), ("GO", "Goias"), ("MA", "Maranhao"),
    ("MT", "Mato Grosso"), ("MS", "Mato Grosso do Sul"), ("MG", "Minas Gerais"),
    ("PA", "Para"), ("PB", "Paraiba"), ("PR", "Parana"), ("PE", "Pernambuco"),
    ("PI", "Piaui"), ("RJ", "Rio de Janeiro"), ("RN", "Rio Grande do Norte"),
    ("RS", "Rio Grande do Sul"), ("RO", "Rondonia"), ("RR", "Roraima"),
    ("SC", "Santa Catarina"), ("SP", "Sao Paulo"), ("SE", "Sergipe"),
    ("TO", "Tocantins"),
]
UFS_VALIDAS = {sigla for sigla, _ in UFS}
NOME_UF = dict(UFS)


def normalizar(texto):
    """Tira acento, deixa minusculo e colapsa espacos (para casar nomes)."""
    if not texto:
        return ""
    txt = unicodedata.normalize("NFKD", str(texto))
    txt = "".join(c for c in txt if not unicodedata.combining(c))
    return " ".join(txt.lower().strip().split())


def municipios_por_uf(uf):
    """Nomes de municipios (ordenados) com CAPAG cadastrada para a UF."""
    from app.models import CapagMunicipio
    if not uf:
        return []
    rows = (CapagMunicipio.query
            .filter_by(uf=uf.upper())
            .order_by(CapagMunicipio.nome)
            .all())
    return [r.nome for r in rows]


def consultar(esfera, uf, municipio):
    """Retorna a CAPAG aplicavel (dict) ou None quando nao se aplica.

    Regras combinadas com o Gerson:
    - federal   -> None (nao existe CAPAG para a Uniao)
    - estadual  -> nota do estado (UF)
    - municipal com municipio -> nota do municipio; se nao achar, usa a UF
    - municipal sem municipio  -> nota da UF
    """
    from app.models import CapagEstado, CapagMunicipio

    esfera = (esfera or "").lower().strip()
    uf = (uf or "").upper().strip()

    if esfera == "federal":
        return None
    if esfera not in ("estadual", "municipal"):
        return None
    if uf not in UFS_VALIDAS:
        return None

    if esfera == "municipal" and municipio and municipio.strip():
        alvo = normalizar(municipio)
        m = (CapagMunicipio.query
             .filter_by(uf=uf, nome_normalizado=alvo)
             .first())
        if m:
            return {
                "nota": m.classificacao,
                "ambito": "municipio",
                "local": f"{m.nome}/{uf}",
                "referencia": m.referencia,
            }
        # municipio nao encontrado -> cai para a UF

    e = CapagEstado.query.filter_by(uf=uf).first()
    if e:
        return {
            "nota": e.classificacao,
            "ambito": "estado",
            "local": NOME_UF.get(uf, uf),
            "referencia": e.referencia,
        }
    return None


def significado(nota):
    """Explicacao curta da nota, para exibir ao usuario."""
    if not nota:
        return ""
    n = nota.strip().upper()
    if n in {"A+", "A", "B+", "B"}:
        return "Boa capacidade de pagamento (apta a obter garantia da Uniao)."
    if n in {"C", "D"}:
        return "Capacidade de pagamento fraca (atencao ao risco fiscal do ente)."
    return ""
