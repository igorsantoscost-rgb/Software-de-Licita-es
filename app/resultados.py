"""
Resultados por item da disputa: valor vencedor, vitoria/derrota, diferenca
percentual em relacao ao valor minimo do cliente e dados do vencedor (CNPJ).
Tambem calcula os indicadores gerenciais (por licitacao e consolidado).
"""
import re
import logging
import requests
from collections import defaultdict

logger = logging.getLogger(__name__)

RESULTADOS = {
    "vitoria": "Vitória",
    "derrota": "Derrota",
    "derrota_justificada": "Derrota justificada",
    "sem_lance": "Sem valor do cliente",
}
DISPUTADOS = ("vitoria", "derrota", "derrota_justificada")


def so_digitos(txt):
    return re.sub(r"\D", "", txt or "")


def formatar_cnpj(cnpj):
    d = so_digitos(cnpj)
    if len(d) != 14:
        return cnpj or ""
    return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"


def cnpj_valido(cnpj):
    d = so_digitos(cnpj)
    if len(d) != 14 or d == d[0] * 14:
        return False
    def dv(base, pesos):
        s = sum(int(n) * p for n, p in zip(base, pesos))
        r = s % 11
        return "0" if r < 2 else str(11 - r)
    p1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    p2 = [6] + p1
    return d[12] == dv(d[:12], p1) and d[13] == dv(d[:13], p2)


def parse_valor(txt):
    """Aceita 1.234,56 ou 1234.56. Retorna float ou None."""
    t = (txt or "").strip().replace("R$", "").replace(" ", "")
    if not t:
        return None
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    try:
        v = float(t)
    except ValueError:
        return None
    return v if v > 0 else None


def consultar_cnpj(cnpj):
    """Busca razao social, UF e municipio em bases publicas.
    1) vencedores ja gravados no proprio sistema; 2) BrasilAPI; 3) CNPJ.ws; 4) ReceitaWS.
    Retorna dict {nome, uf, municipio, fonte} ou None."""
    d = so_digitos(cnpj)
    if not cnpj_valido(d):
        return None

    from app.models import ItemLicitacao
    ja = (ItemLicitacao.query
          .filter(ItemLicitacao.vencedor_cnpj == d, ItemLicitacao.vencedor_nome.isnot(None))
          .order_by(ItemLicitacao.resultado_em.desc()).first())
    if ja:
        return {"nome": ja.vencedor_nome, "uf": ja.vencedor_uf or "",
                "municipio": ja.vencedor_municipio or "", "fonte": "Bidfy"}

    fontes = [
        ("BrasilAPI", f"https://brasilapi.com.br/api/cnpj/v1/{d}",
         lambda j: (j.get("razao_social"), j.get("uf"), j.get("municipio"))),
        ("CNPJ.ws", f"https://publica.cnpj.ws/cnpj/{d}",
         lambda j: (j.get("razao_social"),
                    ((j.get("estabelecimento") or {}).get("estado") or {}).get("sigla"),
                    ((j.get("estabelecimento") or {}).get("cidade") or {}).get("nome"))),
        ("ReceitaWS", f"https://receitaws.com.br/v1/cnpj/{d}",
         lambda j: (j.get("nome"), j.get("uf"), j.get("municipio"))),
    ]
    for nome_fonte, url, extrair in fontes:
        try:
            r = requests.get(url, timeout=8, headers={"User-Agent": "Bidfy/1.0"})
            if r.status_code != 200:
                continue
            nome, uf, municipio = extrair(r.json())
            if nome:
                return {"nome": nome.strip(), "uf": (uf or "").strip().upper()[:2],
                        "municipio": (municipio or "").strip().title(), "fonte": nome_fonte}
        except Exception as e:
            logger.warning(f"Consulta CNPJ em {nome_fonte} falhou: {e}")
    return None


def _total_item(item):
    v = float(item.valor_vencedor or 0)
    return v * (item.quantidade or 1)


def metricas(itens):
    """Indicadores gerenciais a partir de uma lista de itens (de uma ou varias licitacoes)."""
    com = [i for i in itens if i.resultado]
    disp = [i for i in com if i.resultado in DISPUTADOS]
    vit = [i for i in disp if i.resultado == "vitoria"]
    der = [i for i in disp if i.resultado == "derrota"]
    jus = [i for i in disp if i.resultado == "derrota_justificada"]
    difs = [float(i.diferenca_pct) for i in der if i.diferenca_pct is not None]

    concorrentes = defaultdict(lambda: {"nome": "", "uf": "", "itens": 0, "valor": 0.0})
    for i in der + jus:
        if not i.vencedor_cnpj:
            continue
        c = concorrentes[i.vencedor_cnpj]
        c["nome"] = i.vencedor_nome or formatar_cnpj(i.vencedor_cnpj)
        c["uf"] = i.vencedor_uf or ""
        c["itens"] += 1
        c["valor"] += _total_item(i)
    top = sorted(concorrentes.items(), key=lambda kv: (-kv[1]["itens"], -kv[1]["valor"]))[:5]

    return {
        "com_resultado": len(com),
        "disputados": len(disp),
        "vitorias": len(vit),
        "derrotas": len(der),
        "justificadas": len(jus),
        "pct_exito": (len(vit) / len(disp) * 100) if disp else None,
        "valor_vencido": sum(_total_item(i) for i in vit),
        "valor_perdido": sum(_total_item(i) for i in der + jus),
        "media_acima": (sum(difs) / len(difs)) if difs else None,
        "maior_acima": max(difs) if difs else None,
        "menor_acima": min(difs) if difs else None,
        "provisorios": sum(1 for i in com if not i.resultado_definitivo),
        "concorrentes": [{"cnpj": formatar_cnpj(k), **v} for k, v in top],
    }
