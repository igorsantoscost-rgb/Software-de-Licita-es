"""
Lista completa de itens de uma licitacao, para preencher os Parametros da
disputa (item, descricao, unidade, quantidade, valor estimado).

Fontes, nesta ordem:
  1. PNCP (API publica): a compra e achada pelo link do edital
     (pncp.gov.br/app/editais/<cnpj>/<ano>/<seq>) ou pela busca do PNCP
     (orgao + numero + UF + data da disputa).
  2. Planilha .xlsx anexada ao processo (planilha estimada, relacao de itens...).

O HTML da Effecti so traz os itens que bateram na busca, nao a lista toda,
por isso esta busca existe.
"""
import re
import unicodedata
from datetime import datetime

from app.texto import padronizar_frase

API_PNCP = "https://pncp.gov.br/api/pncp/v1"
BUSCA_PNCP = "https://pncp.gov.br/api/search/"
CABECALHOS = {"User-Agent": "Mozilla/5.0 (Bidfy)", "Accept": "application/json"}
TIMEOUT = (10, 30)

RE_LINK_PNCP = re.compile(r"(?:editais|compras)/(\d{14})/(\d{4})/(\d+)")


def _norm(txt):
    txt = unicodedata.normalize("NFKD", str(txt or ""))
    txt = "".join(c for c in txt if not unicodedata.combining(c)).lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", txt).split())


def _float(v):
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace("R$", "").strip()
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _qtd(v):
    f = _float(v)
    return int(round(f)) if f is not None else None


def _item(numero, descricao, unidade=None, quantidade=None, valor=None, lote=None):
    return {
        "numero_item": (str(numero).strip() if numero not in (None, "") else "")[:20] or None,
        "lote_grupo": (str(lote).strip() if lote else "")[:100] or None,
        "descricao": padronizar_frase(" ".join(str(descricao or "").split()))[:500] or "(sem descrição)",
        "unidade": padronizar_frase(" ".join(str(unidade or "").split()))[:50] or None,
        "quantidade": _qtd(quantidade),
        "valor_estimado": _float(valor),
    }


# ─── PNCP ────────────────────────────────────────────────────────────────────

def ids_no_link(link):
    m = RE_LINK_PNCP.search(link or "")
    return (m.group(1), m.group(2), m.group(3)) if m else None


def itens_pncp(cnpj, ano, seq):
    """Itens da compra no PNCP. Lista vazia se nao achar ou der erro."""
    import requests
    itens, pagina = [], 1
    while pagina <= 20:
        try:
            r = requests.get(f"{API_PNCP}/orgaos/{cnpj}/compras/{ano}/{seq}/itens",
                             params={"pagina": pagina, "tamanhoPagina": 500},
                             headers=CABECALHOS, timeout=TIMEOUT)
        except Exception:
            break
        if r.status_code != 200:
            break
        try:
            dados = r.json()
        except ValueError:
            break
        lote = dados.get("itens") if isinstance(dados, dict) else dados
        if not lote:
            break
        for it in lote:
            desc = it.get("descricao") or ""
            compl = it.get("informacaoComplementar") or ""
            if len(compl) > len(desc) and compl.strip():
                desc = f"{desc} — {compl}" if desc and _norm(desc) not in _norm(compl) else compl
            grupo = it.get("numeroGrupo") or it.get("grupo") or it.get("numeroLote")
            itens.append(_item(it.get("numeroItem"), desc, it.get("unidadeMedida"),
                               it.get("quantidade"), it.get("valorUnitarioEstimado"),
                               f"Grupo {grupo}" if grupo not in (None, "", 0) else None))
        if len(lote) < 500:
            break
        pagina += 1
    itens.sort(key=lambda i: int(i["numero_item"]) if (i["numero_item"] or "").isdigit() else 10**9)
    return itens


def _numero_curto(numero):
    """'PCE 63/2026' -> '63' ; '045/2026' -> '45'."""
    m = re.search(r"(\d+)\s*/\s*\d{4}", numero or "") or re.search(r"(\d+)", numero or "")
    return (m.group(1).lstrip("0") or "0") if m else ""


def buscar_compra_pncp(orgao, numero, uf=None, data=None, objeto=None, municipio=None):
    """Acha a compra no PNCP pela busca do site. Devolve (cnpj, ano, seq) ou None."""
    import requests
    num = _numero_curto(numero)
    if not num:
        return None
    consultas = []
    if orgao:
        consultas.append(f"{orgao} {num}")
    if municipio:
        consultas.append(f"{municipio} {num}")
    if objeto:
        consultas.append(objeto[:150])
    vistos, melhor, nota_melhor = set(), None, 0
    palavras_orgao = {p for p in _norm(f"{orgao} {municipio or ''}").split() if len(p) > 3
                      and p not in ("prefeitura", "municipal", "municipio", "secretaria", "estado")}
    for q in consultas:
        try:
            r = requests.get(BUSCA_PNCP, params={"q": q, "tipos_documento": "edital",
                                                  "ordenacao": "-data", "pagina": 1, "tam_pagina": 20},
                             headers=CABECALHOS, timeout=TIMEOUT)
            dados = r.json() if r.status_code == 200 else {}
        except Exception:
            continue
        for it in (dados.get("items") or []):
            ids = None
            if it.get("orgao_cnpj") and it.get("ano") and it.get("numero_sequencial"):
                ids = (str(it["orgao_cnpj"]), str(it["ano"]), str(it["numero_sequencial"]))
            else:
                ids = ids_no_link(it.get("item_url") or "")
            if not ids or ids in vistos:
                continue
            vistos.add(ids)
            nota = 0
            texto = _norm(f"{it.get('title', '')} {it.get('numero', '')} {it.get('description', '')}")
            if re.search(rf"\b0*{num}\b", texto):
                nota += 2
            if uf and (it.get("uf") or "").upper() == uf:
                nota += 1
            fim = str(it.get("data_fim_vigencia") or it.get("data_encerramento_proposta") or "")[:10]
            if data and fim == data.strftime("%Y-%m-%d"):
                nota += 3
            nome = _norm(f"{it.get('orgao_nome', '')} {it.get('municipio_nome', '')} {it.get('unidade_nome', '')}")
            if palavras_orgao and palavras_orgao & set(nome.split()):
                nota += 1
            if nota > nota_melhor:
                melhor, nota_melhor = ids, nota
        if nota_melhor >= 5:
            break
    # Exige numero + data, ou numero + UF + orgao: evita pegar a compra errada
    return melhor if nota_melhor >= 4 else None


# ─── planilha anexada ────────────────────────────────────────────────────────

def itens_de_planilha(caminho):
    """Le uma planilha .xlsx com cabecalho tipo Item | Descricao | Unidade | Quantidade | Valor."""
    try:
        from openpyxl import load_workbook
        wb = load_workbook(caminho, read_only=True, data_only=True)
    except Exception:
        return []
    melhores = []
    for ws in wb.worksheets:
        idx, itens, lote_atual = None, [], None
        for linha in ws.iter_rows(values_only=True):
            cel = [_norm(c) for c in linha]
            if idx is None:
                tem_desc = any(c.startswith(("descri", "especifica", "objeto", "produto")) for c in cel)
                tem_qtd = any(c.startswith(("quant", "qtd", "qtde")) for c in cel)
                if tem_desc and tem_qtd:
                    idx = {}
                    for i, c in enumerate(cel):
                        if not c:
                            continue
                        if c.startswith(("descri", "especifica", "objeto", "produto")) and "descricao" not in idx:
                            idx["descricao"] = i
                        elif c.startswith(("quant", "qtd", "qtde")) and "quantidade" not in idx:
                            idx["quantidade"] = i
                        elif c.startswith(("unid", "und", "un ", "medida")) or c in ("un", "und", "u m", "um"):
                            idx.setdefault("unidade", i)
                        elif c.startswith(("lote", "grupo")):
                            idx.setdefault("lote", i)
                        elif c.startswith("item") or c in ("n", "no", "n item", "numero", "seq"):
                            idx.setdefault("item", i)
                        elif ("unit" in c or "medio" in c or "estimado" in c) and ("valor" in c or "preco" in c):
                            idx.setdefault("valor", i)
                continue

            def col(nome):
                i = idx.get(nome)
                return linha[i] if i is not None and i < len(linha) else None

            desc = col("descricao")
            if not desc or not str(desc).strip():
                # linha "LOTE 01" sozinha separando grupos
                txt = " ".join(str(c) for c in linha if c not in (None, ""))
                m = re.match(r"^\s*(lote|grupo)\s*[:nº°o.]*\s*(\d+)", txt, re.I)
                if m:
                    lote_atual = f"{m.group(1).capitalize()} {m.group(2)}"
                continue
            if _norm(desc).startswith(("total", "valor total")):
                continue
            if col("quantidade") is None and col("item") is None:
                continue
            itens.append(_item(col("item"), desc, col("unidade"), col("quantidade"),
                               col("valor"), col("lote") or lote_atual))
        if len(itens) > len(melhores):
            melhores = itens
    wb.close()
    return melhores


def _parece_planilha_de_itens(nome):
    n = _norm(nome)
    return any(p in n for p in ("planilha", "itens", "orcament", "estimad", "relacao", "quantitativ",
                                "termo de referencia", "proposta", "precos", "anexo"))


def buscar_itens(link="", orgao="", numero="", uf=None, data=None, objeto="", municipio=None,
                 arquivos=()):
    """Devolve (itens, fonte). fonte: 'PNCP' | 'planilha <nome>' | None.
    arquivos: [(nome, caminho)] ja baixados/anexados ao processo."""
    ids = ids_no_link(link)
    if not ids:
        ids = buscar_compra_pncp(orgao, numero, uf, data, objeto, municipio)
    if ids:
        itens = itens_pncp(*ids)
        if itens:
            return itens, "PNCP"
    planilhas = [(n, c) for n, c in arquivos if n.lower().endswith((".xlsx", ".xlsm"))]
    planilhas.sort(key=lambda a: not _parece_planilha_de_itens(a[0]))
    for nome, caminho in planilhas:
        itens = itens_de_planilha(caminho)
        if len(itens) >= 1:
            return itens, f"planilha {nome}"
    return [], None
