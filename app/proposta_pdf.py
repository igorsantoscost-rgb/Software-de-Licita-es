"""
Leitura de propostas de precos em PDF (texto + tabela), sem IA.

Extrai do PDF:
  - CNPJ do licitante (e, pela base publica, razao social / UF / cidade)
  - por item: numero, quantidade, valor unitario, valor total e marca

Funciona com PDFs que tem texto (gerados pelo Word, sistemas etc.).
PDF escaneado (imagem) nao tem texto e e recusado com aviso.
"""
import re
import subprocess

RE_CNPJ = re.compile(r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b")
RE_DINHEIRO = re.compile(r"(?:R\$\s*)?\d{1,3}(?:\.\d{3})*,\d{2,4}\b|(?:R\$\s*)\d+(?:,\d{2,4})?\b")
RE_LINHA_ITEM = re.compile(r"^\s{0,8}(?:item\s*)?(\d{1,4})(?:\s{2,}|\s+(?=[A-ZÁÉÍÓÚÂÊÔÃÕÇ]))", re.IGNORECASE)
RE_NUM = re.compile(r"^\d{1,3}(?:\.\d{3})*(?:,\d+)?$|^\d+(?:,\d+)?$")


def texto_do_pdf(caminho):
    """Texto com layout preservado (colunas alinhadas)."""
    try:
        out = subprocess.run(["pdftotext", "-layout", caminho, "-"],
                             capture_output=True, timeout=60, check=True)
        return out.stdout.decode("utf-8", errors="ignore")
    except Exception:
        try:
            from pypdf import PdfReader
            return "\n".join((p.extract_text() or "") for p in PdfReader(caminho).pages)
        except Exception:
            return ""


def palavras_do_pdf(caminho):
    """Palavras com coordenadas (pdftotext -bbox). y acumulado entre paginas."""
    import html as _html
    try:
        out = subprocess.run(["pdftotext", "-bbox", caminho, "-"], capture_output=True, timeout=60, check=True)
        txt = out.stdout.decode("utf-8", errors="ignore")
    except Exception:
        return []
    palavras, desloc, altura = [], 0.0, 0.0
    for bloco in re.finditer(r'<page width="([\d.]+)" height="([\d.]+)">(.*?)</page>', txt, re.S):
        altura = float(bloco.group(2))
        for w in re.finditer(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>', bloco.group(3)):
            x0, y0, x1, y1 = (float(w.group(i)) for i in range(1, 5))
            palavras.append({"x0": x0, "x1": x1, "y0": y0 + desloc, "y1": y1 + desloc,
                             "xc": (x0 + x1) / 2, "yc": (y0 + y1) / 2 + desloc, "t": _html.unescape(w.group(5))})
        desloc += altura
    return palavras


def _marcas_por_coluna(palavras, numeros):
    """Usa a posicao real da coluna MARCA (cabecalho) para pegar a marca de cada item.
    numeros: lista de numeros de item na ordem em que aparecem. Retorna {indice: marca}."""
    if not palavras:
        return {}
    cab = None
    for w in palavras:
        if re.match(r"^MARCA\b", w["t"].upper()):
            mesma = [o for o in palavras if abs(o["yc"] - w["yc"]) < 4 and o is not w]
            txt = " ".join(o["t"].upper() for o in mesma)
            if any(k in txt for k in ("ITEM", "VALOR", "QTD", "QUANT", "UNIT", "DESCRI")):
                cab = (w, mesma)
                break
    if not cab:
        return {}
    marca, mesma = cab
    direita = [o["x0"] for o in mesma if o["x0"] > marca["x1"]]
    x_dir = min(direita) - 2 if direita else 10 ** 6
    item_cab = next((o for o in mesma if o["t"].upper().startswith("ITEM")), None)

    # linha (y) de cada item: numero do item na coluna ITEM, abaixo do cabecalho
    ys, ultimo_y = [], marca["y1"]
    for n in numeros:
        cand = [w for w in palavras if w["y0"] > ultimo_y and (w["t"].lstrip("0") or "0") == n
                and (item_cab is None or abs(w["xc"] - item_cab["xc"]) < 30 or w["x0"] < item_cab["x1"] + 15)]
        if not cand:
            ys.append(None)
            continue
        y = min(cand, key=lambda w: w["y0"])["yc"]
        ys.append(y)
        ultimo_y = y
    fim_tabela = min([w["yc"] for w in palavras if w["yc"] > (max([y for y in ys if y] or [0])) and "TOTAL" in w["t"].upper()] or [10 ** 9])

    # borda esquerda real da coluna: fim do texto que fica todo a esquerda do titulo MARCA
    corpo = [w for w in palavras if marca["y1"] < w["yc"] < fim_tabela]
    esquerda = [w["x1"] for w in corpo if w["x1"] <= marca["x0"] + 2]
    x_esq = max(esquerda) if esquerda else marca["x0"] - 25

    marcas = {}
    for k, y in enumerate(ys):
        if y is None:
            continue
        ant = next((ys[j] for j in range(k - 1, -1, -1) if ys[j]), None)
        prox = next((ys[j] for j in range(k + 1, len(ys)) if ys[j]), None)
        topo = (ant + y) / 2 if ant else marca["y1"] + 1
        base = (y + prox) / 2 if prox else min(fim_tabela - 2, y + 40)
        ws = [w for w in palavras if topo <= w["yc"] < base and w["x0"] > x_esq - 0.5 and w["xc"] <= x_dir
              and not RE_DINHEIRO.fullmatch(w["t"]) and w["t"] not in ("R$",)]
        ws.sort(key=lambda w: (round(w["yc"] / 3), w["x0"]))
        texto = " ".join(w["t"] for w in ws).strip()
        if texto:
            marcas[k] = texto
    return marcas


RE_MARCA_INLINE = re.compile(r"\bMA?R?CA(?:\s*(?:OFERTADA|/\s*MODELO|E\s+MODELO|/\s*FABRICANTE))?\s*[:\-–]\s*(.+)", re.IGNORECASE)


def _valor(txt):
    t = txt.replace("R$", "").replace(" ", "").strip()
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return None


def _segmentos(linha):
    """Trechos de texto separados por 2+ espacos, com a coluna onde comecam."""
    return [(m.start(), m.group().strip()) for m in re.finditer(r"\S+(?: \S+)*", linha)]


def _coluna_marca(linhas):
    """(inicio, fim) da coluna MARCA pelo cabecalho da tabela; fim = inicio da proxima coluna."""
    for ln in linhas:
        up = ln.upper()
        if "MARCA" in up and ("ITEM" in up or "QTD" in up or "QUANT" in up or "UNIT" in up or "VALOR" in up or "DESCRI" in up):
            ini = up.index("MARCA")
            depois = [c for c, _ in _segmentos(up) if c > ini + 2]
            return ini, (depois[0] if depois else 10 ** 6), linhas.index(ln)
    # cabecalho quebrado em varias linhas: procura MARCA perto de linha com ITEM
    for i, ln in enumerate(linhas):
        up = ln.upper()
        if "MARCA" in up:
            vizinhas = " ".join(linhas[max(0, i - 2): i + 3]).upper()
            if "ITEM" in vizinhas and ("VALOR" in vizinhas or "QTD" in vizinhas or "QUANT" in vizinhas):
                ini = up.index("MARCA")
                depois = [c for c, _ in _segmentos(up) if c > ini + 2]
                return ini, (depois[0] if depois else 10 ** 6), i
    return None


def ler_proposta(caminho, cnpjs_ignorar=()):
    texto = texto_do_pdf(caminho)
    if len(texto.strip()) < 40:
        return {"ok": False, "erro": "Este PDF não tem texto legível (parece escaneado). Preencha o resultado à mão."}

    linhas = texto.splitlines()

    # ── Licitante ──
    ignorar = {re.sub(r"\D", "", c) for c in cnpjs_ignorar if c}
    # CNPJ do licitante: o que mais se repete (cabecalho, rodape, assinatura digital);
    # o CNPJ do orgao, quando aparece, costuma vir uma vez so. Empate: o primeiro.
    contagem, ordem = {}, []
    for m in RE_CNPJ.finditer(texto):
        d = re.sub(r"\D", "", m.group())
        if len(d) == 14 and d not in ignorar:
            if d not in contagem:
                ordem.append(d)
            contagem[d] = contagem.get(d, 0) + 1
    cnpj = max(ordem, key=lambda d: (contagem[d], -ordem.index(d))) if ordem else None
    nome_pdf = None
    for ln in linhas:
        s = ln.strip()
        if re.search(r"\b(LTDA|EIRELI|S\.?A\.?|ME|EPP|COMERCIO|COMÉRCIO|INDUSTRIA|INDÚSTRIA)\b", s.upper()) and len(s) > 8:
            nome_pdf = re.split(r"\s[-–]\s|CNPJ|,", re.sub(r"^.*?:\s*", "", s))[0].strip()
            break

    # ── Itens ──
    col_marca = _coluna_marca(linhas)
    idx_itens = []
    for i, ln in enumerate(linhas):
        m = RE_LINHA_ITEM.match(ln)
        if not m:
            continue
        valores = [v for v in RE_DINHEIRO.finditer(ln) if v.start() > m.end()]
        if not valores:
            continue
        # com "R$" na linha, so valem os que tem R$ (evita medidas da descricao, ex: 0,50 mm)
        com_rs = [v for v in valores if "R$" in v.group()]
        valores = com_rs if com_rs else valores[-2:]
        idx_itens.append((i, m, valores))

    itens = []
    for pos, (i, m, valores) in enumerate(idx_itens):
        ln = linhas[i]
        numero = m.group(1).lstrip("0") or "0"
        nums = [_valor(v.group()) for v in valores]
        nums = [n for n in nums if n is not None]
        # quantidade: ultimo numero "solto" antes do primeiro valor em dinheiro
        antes = ln[m.end():valores[0].start()]
        qtd = None
        for tok in reversed(antes.split()):
            if RE_NUM.match(tok):
                qtd = _valor(tok) if "," in tok else float(tok.replace(".", ""))
                break
        unit = nums[0] if nums else None
        total = nums[1] if len(nums) > 1 else None
        if unit and total and qtd and abs(unit * qtd - total) > max(0.05, total * 0.01):
            # ordem trocada (total antes do unitario) ou unitario com mais casas
            if abs(total * qtd - unit) <= max(0.05, unit * 0.01):
                unit, total = total, unit
        if unit and not total and qtd:
            total = round(unit * qtd, 2)
        if unit and total and not qtd and unit > 0:
            qtd = round(total / unit, 3)

        # marca: texto na coluna MARCA, nas linhas mais proximas deste item
        marca = None
        if col_marca is not None:
            ini = idx_itens[pos - 1][0] + 1 if pos > 0 else max(0, i - 4)
            fim = idx_itens[pos + 1][0] if pos + 1 < len(idx_itens) else min(len(linhas), i + 5)
            partes = []
            for j in range(max(ini, col_marca[2] + 1), fim):
                # linha mais perto deste item do que dos vizinhos
                d_aqui = abs(j - i)
                d_ant = abs(j - idx_itens[pos - 1][0]) if pos > 0 else 999
                d_prox = abs(j - idx_itens[pos + 1][0]) if pos + 1 < len(idx_itens) else 999
                # empate entre dois itens: a linha fica com o item de baixo
                # (celulas de varias linhas costumam ficar centralizadas no numero do item)
                if d_aqui > d_ant or d_aqui > d_prox or (d_aqui == d_prox and j > i) or d_aqui > 3:
                    continue
                for c, seg in _segmentos(linhas[j]):
                    if col_marca[0] - 8 <= c < col_marca[1] - 1 and not RE_DINHEIRO.fullmatch(seg) and "TOTAL" not in seg.upper():
                        partes.append(seg)
            marca = " ".join(partes).strip() or None

        # "MARCA OFERTADA: X" escrita dentro do bloco do item (abaixo da descricao)
        if not marca:
            fim_bloco = idx_itens[pos + 1][0] if pos + 1 < len(idx_itens) else min(len(linhas), i + 15)
            for j in range(i, fim_bloco):
                mm = RE_MARCA_INLINE.search(linhas[j])
                if mm and not re.search(r"\bVALOR\b", linhas[j], re.I):
                    marca = re.split(r"\s{3,}", mm.group(1).strip())[0].strip() or None
                    break

        itens.append({"numero": numero, "qtd": qtd, "unit": unit, "total": total, "marca": marca})

    # Marca pela posicao real da coluna (PDFs exportados do Excel desalinham o texto)
    if itens and any(not it["marca"] for it in itens) or (itens and col_marca is None):
        por_coluna = _marcas_por_coluna(palavras_do_pdf(caminho), [it["numero"] for it in itens])
        for k, it in enumerate(itens):
            if not it["marca"] and por_coluna.get(k):
                it["marca"] = por_coluna[k]

    if not itens:
        return {"ok": False, "erro": "Não encontrei a tabela de itens nesta proposta. Preencha o resultado à mão.",
                "cnpj": cnpj, "nome_pdf": nome_pdf}
    return {"ok": True, "cnpj": cnpj, "nome_pdf": nome_pdf, "itens": itens}


if __name__ == "__main__":
    import sys, json
    print(json.dumps(ler_proposta(sys.argv[1]), ensure_ascii=False, indent=1))
