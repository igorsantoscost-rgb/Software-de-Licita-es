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
    elif re.fullmatch(r"\d{1,3}(?:\.\d{3})+", s):  # "5.000" = cinco mil
        s = s.replace(".", "")
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
        "descricao": padronizar_frase(re.sub(r"\s+([,.;:)])", r"\1", " ".join(str(descricao or "").split())))[:500]
                     or "(sem descrição)",
        "unidade": padronizar_frase(" ".join(str(unidade or "").split()))[:50] or None,
        "quantidade": _qtd(quantidade),
        "valor_estimado": _float(valor),
    }


# ─── PNCP ────────────────────────────────────────────────────────────────────

# Arquivos do PNCP vem nomeados <cnpj>_<ano>_<sequencial>_<n>_<nome> (tambem nos links da Effecti)
RE_NOME_PNCP = re.compile(r"(?<!\d)(\d{14})_(20\d\d)_0*(\d{1,6})_\d{1,3}_")


def ids_no_link(link):
    m = RE_LINK_PNCP.search(link or "")
    return (m.group(1), m.group(2), m.group(3)) if m else None


def ids_nos_textos(*textos):
    """Codigo da compra no PNCP a partir do link ou do nome/URL de um anexo."""
    for t in textos:
        ids = ids_no_link(t)
        if ids:
            return ids
        m = RE_NOME_PNCP.search(t or "")
        if m:
            return (m.group(1), m.group(2), m.group(3))
    return None


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


# ─── tabelas (planilha, Word, .doc) ──────────────────────────────────────────

UNIDADES = {
    "un", "und", "unid", "unidade", "unidades", "ud", "cx", "caixa", "pct", "pc", "pca", "peca", "pacote",
    "kg", "g", "l", "lt", "litro", "litros", "ml", "gl", "galao", "lata", "m", "m2", "m3", "ml", "mt",
    "metro", "metros", "rolo", "rl", "fr", "frasco", "balde", "bd", "sc", "saco", "tb", "tubo", "par",
    "jg", "jogo", "kit", "cj", "conj", "conjunto", "serv", "servico", "mes", "meses", "dia", "diaria",
    "hora", "h", "dz", "duzia", "resma", "rs", "fd", "fardo", "bl", "bloco", "ton", "t", "km", "cartela",
    "embalagem", "emb", "vb", "verba", "sv", "pote", "bisnaga", "bobina", "envelope", "folha", "cento",
    "milheiro", "ampola", "cp", "comprimido", "capsula", "ui", "refil", "carga",
}


def _mapear_cabecalho(celulas):
    """Celulas do cabecalho -> {"descricao": i, "quantidade": i, ...} ou None."""
    cel = [_norm(c) for c in celulas]
    tem_desc = any(c.startswith(("descri", "especifica", "objeto", "produto", "discrimina", "material"))
                   for c in cel)
    tem_qtd = any(c.startswith(("quant", "qtd", "qtde", "qde", "qt ")) or c in ("qt", "qtd", "qtde")
                  for c in cel)
    if not (tem_desc and tem_qtd):
        return None
    idx = {}
    for i, c in enumerate(cel):
        if not c:
            continue
        if c.startswith(("descri", "especifica", "objeto", "produto", "discrimina", "material")) \
                and "descricao" not in idx:
            idx["descricao"] = i
        elif (c.startswith(("quant", "qtd", "qtde", "qde", "qt ")) or c == "qt") and "quantidade" not in idx:
            idx["quantidade"] = i
        elif c.startswith(("unid", "und", "un ", "medida")) or c in ("un", "und", "u m", "um", "ud"):
            idx.setdefault("unidade", i)
        elif c.startswith(("lote", "grupo")):
            idx.setdefault("lote", i)
        elif c.startswith("item") or c in ("n", "no", "n item", "numero", "seq", "ord", "n o"):
            idx.setdefault("item", i)
        elif ("valor" in c or "preco" in c or c.startswith(("vl", "vlr", "v unit"))) and "total" not in c:
            idx.setdefault("valor", i)
    return idx if "descricao" in idx and "quantidade" in idx else None


RE_LOTE = re.compile(r"^\s*(lote|grupo)\s*(?:n[º°o.]*\s*)?[:\-]?\s*0*(\d+)", re.I)


def itens_de_linhas(linhas):
    """Recebe as linhas de uma ou mais tabelas (listas de celulas, na ordem do
    documento; linhas de texto solto viram listas de 1 celula) e devolve os itens."""
    idx, itens, lote_atual = None, [], None
    for linha in linhas:
        linha = list(linha)
        textos = [str(c).strip() for c in linha if c not in (None, "") and str(c).strip()]
        if not textos:
            continue
        m = RE_LOTE.match(" ".join(textos))
        if m and (len(textos) <= 2):
            lote_atual = f"{m.group(1).capitalize()} {m.group(2)}"
            continue
        novo = _mapear_cabecalho(linha)
        if novo:
            idx = novo
            continue
        if idx is None or len(linha) < 2:
            continue

        def col(nome):
            i = idx.get(nome)
            return linha[i] if i is not None and i < len(linha) else None

        desc = col("descricao")
        if not desc or not str(desc).strip():
            continue
        nd = _norm(desc)
        if nd.startswith(("total", "valor total", "subtotal")):
            continue
        qtd = _float(col("quantidade"))
        if qtd is None:
            continue
        numero = col("item")
        if numero not in (None, "") and not re.fullmatch(r"\s*\d{1,4}(?:\.\d{1,3})?\s*", str(numero)):
            continue  # linha de outra tabela que caiu aqui
        itens.append(_item(numero, desc, col("unidade"), qtd, col("valor"), col("lote") or lote_atual))
    # numeracao: se nao veio, numera em sequencia
    if itens and not any(i["numero_item"] for i in itens):
        for n, it in enumerate(itens, 1):
            it["numero_item"] = str(n)
    return itens


def _linhas_xlsx(caminho):
    from openpyxl import load_workbook
    wb = load_workbook(caminho, read_only=True, data_only=True)
    try:
        for ws in wb.worksheets:
            yield [list(r) for r in ws.iter_rows(values_only=True)]
    finally:
        wb.close()


def itens_de_planilha(caminho):
    """Planilha .xlsx com cabecalho tipo Item | Descricao | Unidade | Quantidade | Valor."""
    melhores = []
    try:
        for linhas in _linhas_xlsx(caminho):
            itens = itens_de_linhas(linhas)
            if len(itens) > len(melhores):
                melhores = itens
    except Exception:
        return []
    return melhores


def _linhas_docx(caminho):
    """Paragrafos e linhas de tabela do Word, na ordem em que aparecem."""
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    doc = Document(caminho)
    linhas = []
    for el in doc.element.body.iterchildren():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag == "p":
            txt = Paragraph(el, doc).text.strip()
            if txt:
                linhas.append([txt])
        elif tag == "tbl":
            for row in Table(el, doc).rows:
                vistos, celulas = set(), []
                for c in row.cells:  # celula mesclada aparece repetida
                    if id(c._tc) in vistos:
                        continue
                    vistos.add(id(c._tc))
                    celulas.append(" ".join(c.text.split()))
                linhas.append(celulas)
    return linhas


def _linhas_odt(caminho):
    import zipfile
    import xml.etree.ElementTree as ET
    ns = {"table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
          "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0"}
    with zipfile.ZipFile(caminho) as z:
        raiz = ET.fromstring(z.read("content.xml"))
    linhas = []
    corpo = raiz.find(".//{urn:oasis:names:tc:opendocument:xmlns:office:1.0}text")
    for el in (corpo if corpo is not None else []):
        tag = el.tag.rsplit("}", 1)[-1]
        if tag in ("p", "h"):
            txt = "".join(el.itertext()).strip()
            if txt:
                linhas.append([txt])
        elif tag == "table":
            for tr in el.iter(f"{{{ns['table']}}}table-row"):
                celulas = []
                for tc in tr.findall("table:table-cell", ns):
                    rep = int(tc.get(f"{{{ns['table']}}}number-columns-repeated", "1") or 1)
                    txt = " ".join("".join(tc.itertext()).split())
                    celulas.extend([txt] * min(rep, 30))
                linhas.append(celulas)
    return linhas


def _linhas_doc(caminho):
    """Word antigo (.doc): o antiword desenha as tabelas com '|'."""
    import subprocess
    try:
        out = subprocess.run(["antiword", "-w", "0", caminho], capture_output=True, timeout=60)
        txt = out.stdout.decode("utf-8", errors="ignore")
    except Exception:
        return []
    linhas = []
    for bruta in txt.splitlines():
        if bruta.strip().startswith("|"):
            linhas.append([c.strip() for c in bruta.strip().strip("|").split("|")])
        elif bruta.strip():
            linhas.append([bruta.strip()])
    # antiword quebra a celula em varias linhas: junta as continuacoes (sem numero de item)
    juntas = []
    for l in linhas:
        if (juntas and len(l) > 1 and len(juntas[-1]) == len(l)
                and not any(re.search(r"\d", x) for x in (l[0],)) and not _mapear_cabecalho(l)
                and not any(_float(x) is not None for x in l)):
            juntas[-1] = [f"{a} {b}".strip() for a, b in zip(juntas[-1], l)]
        else:
            juntas.append(l)
    return juntas


# ─── PDF (posicao das palavras) ──────────────────────────────────────────────

def _palavras_pdf(caminho):
    from app.proposta_pdf import palavras_do_pdf
    return palavras_do_pdf(caminho)


def _rotulo_coluna(txt):
    c = _norm(txt)
    if c.startswith(("descri", "especifica", "objeto", "produto", "discrimina", "material")):
        return "descricao"
    if c.startswith(("quant", "qtd", "qtde", "qde")) or c in ("qt",):
        return "quantidade"
    if c.startswith(("unid", "und", "medida")) or c in ("un", "ud", "um", "u", "u m"):
        return "unidade"
    if c.startswith("item") or c in ("n", "no", "nº", "seq", "ord"):
        return "item"
    if c.startswith(("valor", "preco", "vl", "vlr")):
        return "valor"
    if c.startswith(("lote", "grupo")):
        return "lote"
    if c.startswith("marca"):
        return "marca"
    return "outra"


VOCAB_CABECALHO = {
    "produto", "produtos", "servico", "servicos", "unit", "unitario", "unitaria", "total", "r", "rs",
    "catmat", "catser", "codigo", "cod", "medida", "estimado", "estimada", "medio", "media", "maximo",
    "de", "do", "da", "dos", "das", "e", "o", "a", "item", "itens", "valor", "preco", "referencia",
    "especificacao", "especificacoes", "detalhada", "tecnica", "quantidade", "qtd", "qtde", "unidade",
    "und", "fornecimento", "anual", "mensal", "estimativa", "cotado", "ref", "marca", "modelo", "siga",
    "br", "minima", "maxima", "por", "em", "un", "x", "u", "m", "um", "sesi", "senai", "registrada",
    "registrado", "global", "lote", "grupo", "descricao", "estima", "do", "unitari", "esti", "ado",
}


def _linhas_pdf(palavras):
    palavras = sorted(palavras, key=lambda w: (w["yc"], w["x0"]))
    linhas, atual = [], []
    for w in palavras:
        if atual and abs(w["yc"] - atual[0]["yc"]) > 3:
            linhas.append(sorted(atual, key=lambda x: x["x0"]))
            atual = []
        atual.append(w)
    if atual:
        linhas.append(sorted(atual, key=lambda x: x["x0"]))
    return linhas


PALAVRAS_FORTES_CABECALHO = {
    "qtd", "qtde", "quant", "quantidade", "sesi", "senai", "total", "unitario", "unitari", "unit",
    "estimado", "estimada", "estima", "valor", "preco", "medida", "catmat", "catser", "codigo", "cod",
    "u", "um", "un", "und", "unid", "unidade", "rs", "item", "descricao", "especificacao", "marca",
    "modelo", "lote", "grupo", "produto", "medio", "media", "maximo", "registrada", "registrado",
}


def _linha_de_cabecalho(linha, principal=False):
    """Linha so com palavras de cabecalho (sem numeros). As linhas extras de um
    cabecalho de varias linhas precisam ter alguma palavra tipica de coluna."""
    if any(re.search(r"\d", w["t"]) for w in linha):
        return False
    normas = [_norm(w["t"]) for w in linha]
    conhecidas = sum(1 for n, w in zip(normas, linha) if n in VOCAB_CABECALHO or _rotulo_coluna(w["t"]) != "outra")
    if conhecidas * 2 < len(linha):
        return False
    return principal or any(p in PALAVRAS_FORTES_CABECALHO for n in normas for p in n.split())


def _colunas_do_cabecalho(linhas_cab):
    """Junta as palavras de um cabecalho de uma ou varias linhas em colunas
    (pela posicao horizontal) e da nome a cada coluna."""
    palavras = sorted((w for l in linhas_cab for w in l), key=lambda w: w["x0"])
    grupos = []
    for w in palavras:
        g = next((g for g in grupos if w["x0"] <= g["x1"] + 3 and w["x1"] >= g["x0"] - 3), None)
        if g:
            g["x0"], g["x1"] = min(g["x0"], w["x0"]), max(g["x1"], w["x1"])
            g["p"].append(w)
        else:
            grupos.append({"x0": w["x0"], "x1": w["x1"], "p": [w]})
    grupos.sort(key=lambda g: g["x0"])
    colunas = []
    for g in grupos:
        txt = _norm(" ".join(w["t"] for w in sorted(g["p"], key=lambda w: (w["yc"], w["x0"]))))
        junto = txt.replace(" ", "")
        if re.search(r"descri|especifica|discrimina|produto|objeto", junto):
            r = "descricao"
        elif re.search(r"qtd|quant|qtde", junto):
            r = "quantidade"
        elif re.search(r"valor|preco|vlr", junto):
            r = "valor_total" if "total" in junto and not re.search(r"unit", junto) else "valor"
        elif re.search(r"unid|und|medida", junto) or txt in ("u m", "um", "un", "ud"):
            r = "unidade"
        elif re.search(r"^item|^n$|^no$|^seq", txt):
            r = "item"
        elif re.search(r"^lote|^grupo", txt):
            r = "lote"
        elif "marca" in txt:
            r = "marca"
        else:
            r = "outra"
        colunas.append({"rotulo": r, "x0": g["x0"], "x1": g["x1"], "txt": junto})
    # varias colunas de quantidade (ex.: SESI | SENAI | TOTAL): vale a TOTAL, ou a ultima
    qtds = [c for c in colunas if c["rotulo"] == "quantidade"]
    if len(qtds) > 1:
        fica = next((c for c in qtds if "total" in c["txt"]), qtds[-1])
        for c in qtds:
            if c is not fica:
                c["rotulo"] = "outra"
    vals = [c for c in colunas if c["rotulo"] == "valor"]
    for c in vals[1:]:
        c["rotulo"] = "valor_total"
    return colunas


def _montar_linhas_da_tabela(blocos):
    """blocos: linhas da tabela (dicts com 'cols', 'ancora', 'y').
    Liga cada linha ao item certo: se o numero do item fica no topo da celula,
    as linhas seguintes sao dele; se fica centralizado (ha descricao antes do
    primeiro numero), cada linha vai para o numero mais proximo na vertical."""
    ancoras = [i for i, b in enumerate(blocos) if b["ancora"]]
    if not ancoras:
        return []
    centralizado = any(blocos[i]["cols"].get("descricao") for i in range(0, ancoras[0]))
    dono = {}
    if centralizado:
        # o numero fica no meio da celula: a celula vai da primeira linha antes
        # dele ate a mesma distancia depois dele
        inicio = 0
        for k, a in enumerate(ancoras):
            prox = ancoras[k + 1] if k + 1 < len(ancoras) else len(blocos)
            acima = [i for i in range(inicio, a) if blocos[i]["cols"]]
            topo = min((blocos[i]["y"] for i in acima if blocos[i]["cols"].get("descricao")), default=blocos[a]["y"])
            limite = blocos[a]["y"] + (blocos[a]["y"] - topo) + 3
            fim = a
            for i in range(a + 1, prox):
                if blocos[i]["y"] <= limite:
                    fim = i
            for i in range(inicio, fim + 1):
                dono[i] = a
            inicio = fim + 1
    else:
        for i in range(len(blocos)):
            antes = [a for a in ancoras if a <= i]
            if antes:
                dono[i] = antes[-1]
    itens = []
    for a in ancoras:
        linhas = sorted((i for i, d in dono.items() if d == a), key=lambda i: blocos[i]["y"])
        desc, unidade, qtd, valor = [], [], blocos[a]["qtd"], None
        for i in linhas:
            cols = blocos[i]["cols"]
            desc.extend(cols.get("descricao", []))
            if not unidade:
                unidade = [t for t in cols.get("unidade", []) if not re.fullmatch(r"[\d.,]+", t)]
            if qtd is None:
                qtd = next((q for q in (_float(t) for t in cols.get("quantidade", [])) if q is not None), None)
            if valor is None:
                valor = next((t for t in cols.get("valor", []) if re.search(r"\d,\d{2}", t)), None)
        b = blocos[a]
        itens.append({"numero": b["numero"], "desc": desc, "unidade_txt": " ".join(unidade),
                      "qtd": qtd, "valor": valor, "lote": b["lote"]})
    return itens


def itens_de_pdf(caminho):
    """Acha a tabela de itens no PDF pela posicao das colunas do cabecalho
    (cabecalho de uma ou varias linhas)."""
    palavras = _palavras_pdf(caminho)
    if not palavras:
        return []
    linhas = _linhas_pdf(palavras)
    resultado, colunas, lote_atual = [], None, None
    blocos = []

    def fechar():
        nonlocal blocos
        for a in _montar_linhas_da_tabela(blocos):
            if a["desc"] and a["qtd"] is not None:
                resultado.append(_item(a["numero"], " ".join(a["desc"]), a["unidade_txt"],
                                       a["qtd"], a["valor"], a["lote"]))
        blocos = []

    i = 0
    while i < len(linhas):
        linha = linhas[i]
        texto = " ".join(w["t"] for w in linha)
        rotulos = [_rotulo_coluna(w["t"]) for w in linha]
        if "descricao" in rotulos and _linha_de_cabecalho(linha, principal=True):
            # cabecalho pode ocupar varias linhas acima e abaixo desta
            y0 = linha[0]["yc"]
            ini, fim = i, i
            while ini - 1 >= 0 and y0 - linhas[ini - 1][0]["yc"] < 30 and _linha_de_cabecalho(linhas[ini - 1]):
                ini -= 1
            while fim + 1 < len(linhas) and linhas[fim + 1][0]["yc"] - y0 < 30 and _linha_de_cabecalho(linhas[fim + 1]):
                fim += 1
            cand = _colunas_do_cabecalho(linhas[ini:fim + 1])
            if any(c["rotulo"] == "quantidade" for c in cand) and any(c["rotulo"] == "descricao" for c in cand):
                fechar()
                colunas = cand
                i = fim + 1
                continue
        m = RE_LOTE.match(texto)
        if m and len(linha) <= 6:
            fechar()
            lote_atual = f"{m.group(1).capitalize()} {m.group(2)}"
            i += 1
            continue
        if not colunas:
            i += 1
            continue
        if _norm(texto).startswith(("total", "valor total", "subtotal")):
            fechar()
            i += 1
            continue

        def coluna_de(w):
            escolhida = colunas[0]
            for c in colunas:
                if w["xc"] >= c["x0"] - 8:
                    escolhida = c
            return escolhida["rotulo"]

        cols = {}
        for w in linha:
            cols.setdefault(coluna_de(w), []).append(w["t"])
        primeira = linha[0]
        qtd = next((q for q in (_float(t) for t in cols.get("quantidade", [])) if q is not None), None)
        col_primeira = coluna_de(primeira)
        ancora = bool(re.fullmatch(r"0*\d{1,4}(?:\.\d{1,3})?", primeira["t"])
                      and (col_primeira == "item" or (col_primeira in ("lote", "outra") and qtd is not None)))
        if blocos and not ancora and linha[0]["yc"] - blocos[-1]["y"] > 40:
            # Longe da ultima linha (quebra de pagina, rodape, fim da tabela)
            if set(cols) - {"descricao", "unidade"} or re.match(r"^p[aá]gina\s+\d", texto, re.I):
                if not any(re.search(r"\d,\d{2}", t) for t in cols.get("valor", [])):
                    if linha[0]["yc"] - blocos[-1]["y"] > 120:
                        fechar()
                        colunas = None if not blocos else colunas
                    i += 1
                    continue
        blocos.append({"cols": cols, "ancora": ancora, "y": linha[0]["yc"], "numero": primeira["t"].lstrip("0") or "0",
                       "qtd": qtd, "lote": lote_atual})
        i += 1
    fechar()
    return resultado


# ─── escolha da melhor fonte ─────────────────────────────────────────────────

def _parece_planilha_de_itens(nome):
    n = _norm(nome)
    return any(p in n for p in ("planilha", "itens", "orcament", "estimad", "relacao", "quantitativ",
                                "termo de referencia", "proposta", "precos", "anexo"))


def _prioridade_doc(nome):
    n = _norm(nome)
    if "termo de referencia" in n or re.search(r"\btr\b", n) or "projeto basico" in n:
        return 0
    if any(p in n for p in ("planilha", "itens", "orcament", "estimad", "quantitativ", "relacao", "especifica")):
        return 1
    if "proposta" in n or "anexo" in n:
        return 2
    if "edital" in n or "pregao" in n:
        return 3
    return 4


def _qualidade(itens):
    """Mais itens com numero, quantidade e unidade = leitura melhor."""
    return sum(1 + bool(i["unidade"]) + bool(i["numero_item"]) for i in itens)


def itens_de_documento(nome, caminho):
    n = nome.lower()
    try:
        if n.endswith((".xlsx", ".xlsm")):
            return itens_de_planilha(caminho)
        if n.endswith(".docx"):
            return itens_de_linhas(_linhas_docx(caminho))
        if n.endswith(".odt"):
            return itens_de_linhas(_linhas_odt(caminho))
        if n.endswith(".doc"):
            return itens_de_linhas(_linhas_doc(caminho))
        if n.endswith(".pdf"):
            return itens_de_pdf(caminho)
    except Exception:
        return []
    return []


def itens_dos_arquivos(arquivos, limite=8):
    """Le os documentos do processo (TR, planilha, edital...) e fica com a melhor tabela."""
    import os
    docs = [(n, c) for n, c in arquivos
            if n.lower().endswith((".xlsx", ".xlsm", ".docx", ".odt", ".doc", ".pdf"))
            and os.path.exists(c) and os.path.getsize(c) < 60 * 1024 * 1024]
    docs.sort(key=lambda a: _prioridade_doc(a[0]))
    melhor, nome_melhor = [], None
    for nome, caminho in docs[:limite]:
        itens = itens_de_documento(nome, caminho)
        if _qualidade(itens) > _qualidade(melhor):
            melhor, nome_melhor = itens, nome
    return melhor, nome_melhor


def buscar_itens(link="", orgao="", numero="", uf=None, data=None, objeto="", municipio=None,
                 arquivos=(), extras=()):
    """Devolve (itens, fonte). fonte: 'PNCP' | '<nome do arquivo>' | None.
    arquivos: [(nome, caminho)] ja baixados/anexados ao processo."""
    ids = ids_nos_textos(link, *[n for n, _c in arquivos], *extras)
    if not ids:
        ids = buscar_compra_pncp(orgao, numero, uf, data, objeto, municipio)
    if ids:
        itens = itens_pncp(*ids)
        if itens:
            return itens, "PNCP"
    itens, nome = itens_dos_arquivos(arquivos)
    if itens:
        return itens, f"arquivo {nome}"
    return [], None
