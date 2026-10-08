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
    cnpj = None
    for m in RE_CNPJ.finditer(texto):
        d = re.sub(r"\D", "", m.group())
        if len(d) == 14 and d not in ignorar:
            cnpj = d
            break
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

        itens.append({"numero": numero, "qtd": qtd, "unit": unit, "total": total, "marca": marca})

    if not itens:
        return {"ok": False, "erro": "Não encontrei a tabela de itens nesta proposta. Preencha o resultado à mão.",
                "cnpj": cnpj, "nome_pdf": nome_pdf}
    return {"ok": True, "cnpj": cnpj, "nome_pdf": nome_pdf, "itens": itens}


if __name__ == "__main__":
    import sys, json
    print(json.dumps(ler_proposta(sys.argv[1]), ensure_ascii=False, indent=1))
