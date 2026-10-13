"""
Leitura automatica da data de validade de documentos de habilitacao
(certidoes, alvaras, inscricoes...), sem IA.

Ordem de busca no texto do documento:
  1. Data de validade escrita ("Valida ate 05/04/2027", "Validade: 01/10/2026 a 30/10/2026",
     "Vencimento: ...", "Data de validade: ...").
  2. Prazo em dias ("valida por 60 (sessenta) dias") -> emissao + prazo.
  3. Sem validade no documento -> emissao + 90 dias corridos.

Data de emissao: "Emitida em", "Data de emissao", "Expedida em", "Data da consulta",
"Belo Horizonte, 7 de outubro de 2026"... Se nao achar, usa a data do envio.

PDF escaneado (sem texto) nao e lido: devolve sem data e com aviso.
"""
import os
import re
import unicodedata
from datetime import date, timedelta

DIAS_PADRAO = 90

# Documentos que nao vencem: nao recebem validade automatica
TIPOS_SEM_VALIDADE = {
    "contrato_social", "alteracao_contratual", "doc_socio", "doc_conjuge",
    "atestado_tecnico", "balanco_ultimo", "balanco_penultimo",
}

MESES = {
    "janeiro": 1, "fevereiro": 2, "marco": 3, "abril": 4, "maio": 5, "junho": 6,
    "julho": 7, "agosto": 8, "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12,
}

_DATA_NUM = r"(\d{1,2})\s*[/.\-]\s*(\d{1,2})\s*[/.\-]\s*(\d{4}|\d{2})\b"
_DATA_EXT = r"(\d{1,2})\s*(?:o|º)?\s+de\s+(" + "|".join(MESES) + r")\s+de\s+(\d{4})"
RE_DATA = re.compile(_DATA_NUM + "|" + _DATA_EXT)

RE_VALIDADE = re.compile(
    r"(valid[ao]s?\s+ate|validade|vencimento|vence\s+em|valid[ao]\s+no\s+periodo|"
    r"expira(?:\s+em)?|prazo\s+de\s+validade)"
)
RE_PRAZO_DIAS = re.compile(
    r"(?:valid[ao]s?|validade|prazo\s+de\s+validade)[^.\n]{0,40}?"
    r"(?:por|de|:)?\s*(\d{1,3})\s*(?:\([^)]{0,40}\)\s*)?dias"
)
RE_EMISSAO = re.compile(
    r"(emitid[ao]s?|data\s+(?:da|de)\s+emissao|emissao|expedid[ao]|data\s+(?:da|de)\s+expedicao|"
    r"expedicao|gerad[ao]|data\s+(?:da|de)\s+consulta|consulta\s+realizada|"
    r"data\s+(?:da|de)\s+impressao|impress[ao]\s+em|certificamos)"
)


def _normalizar(texto):
    sem_acento = unicodedata.normalize("NFKD", texto)
    sem_acento = "".join(c for c in sem_acento if not unicodedata.combining(c))
    return re.sub(r"[ \t]+", " ", sem_acento.lower())


def _data_do_match(m):
    try:
        if m.group(1):
            d, mes, a = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if a < 100:
                a += 2000
        else:
            d, mes, a = int(m.group(4)), MESES[m.group(5)], int(m.group(6))
        return date(a, mes, d)
    except (ValueError, TypeError, KeyError):
        return None


def _plausivel(dt, hoje):
    return dt is not None and date(hoje.year - 6, 1, 1) <= dt <= date(hoje.year + 8, 12, 31)


def _datas_apos(texto, inicio, janela):
    trecho = texto[inicio:inicio + janela]
    return [(m, _data_do_match(m)) for m in RE_DATA.finditer(trecho)], trecho


def _buscar_validade(texto, hoje):
    candidatos = []
    for kw in RE_VALIDADE.finditer(texto):
        datas, trecho = _datas_apos(texto, kw.end(), 90)
        datas = [(m, d) for m, d in datas if _plausivel(d, hoje)]
        if not datas:
            continue
        m1, d1 = datas[0]
        # "Validade: 60 dias. Emitida em 01/10/2026": a data e da emissao, nao da validade
        if RE_EMISSAO.search(trecho[:m1.start()]) or re.search(r"\d+\s*(?:\([^)]*\)\s*)?dias", trecho[:m1.start()]):
            continue
        escolhida = d1
        # Periodo "01/10/2026 a 30/10/2026": vale a ultima data
        if len(datas) > 1:
            m2, d2 = datas[1]
            entre = trecho[m1.end():m2.start()]
            if re.fullmatch(r"\s*(a|ate|-|–|e)\s*", entre) and d2 >= d1:
                escolhida = d2
        # Data muito longe do termo-chave provavelmente e outra coisa
        if m1.start() > 60:
            continue
        candidatos.append(escolhida)
    if not candidatos:
        return None
    # A data de validade costuma ser a mais distante citada perto do termo
    return max(candidatos)


def _buscar_prazo_dias(texto):
    for m in RE_PRAZO_DIAS.finditer(texto):
        dias = int(m.group(1))
        if 1 <= dias <= 730:
            return dias
    return None


def _buscar_emissao(texto, hoje):
    for kw in RE_EMISSAO.finditer(texto):
        datas, _ = _datas_apos(texto, kw.end(), 70)
        for m, d in datas:
            if _plausivel(d, hoje) and d <= hoje + timedelta(days=1):
                return d
    # "Cidade, 7 de outubro de 2026" (data por extenso no fim do documento)
    datas_ext = []
    for m in re.finditer(r",\s*" + _DATA_EXT, texto):
        try:
            d = date(int(m.group(3)), MESES[m.group(2)], int(m.group(1)))
        except (ValueError, KeyError):
            continue
        if _plausivel(d, hoje) and d <= hoje + timedelta(days=1):
            datas_ext.append(d)
    if datas_ext:
        return max(datas_ext)
    return None


def _texto_do_arquivo(caminho, nome_original):
    ext = os.path.splitext(nome_original or caminho)[1].lower()
    try:
        if ext == ".pdf":
            from app.proposta_pdf import texto_do_pdf
            return texto_do_pdf(caminho)
        if ext == ".docx":
            from app.doc_extractor import extrair_texto_docx
            return extrair_texto_docx(caminho)
        if ext in (".txt", ".html", ".htm"):
            with open(caminho, encoding="utf-8", errors="ignore") as f:
                return f.read()
    except Exception:
        return ""
    return None  # formato que nao da pra ler (imagem etc.)


def analisar_texto(texto, hoje=None):
    """Devolve dict com validade (date), emissao (date|None) e origem/mensagem."""
    hoje = hoje or date.today()
    t = _normalizar(texto)

    emissao = _buscar_emissao(t, hoje)
    validade = _buscar_validade(t, hoje)
    if validade and (emissao is None or validade >= emissao):
        return {"validade": validade, "emissao": emissao, "origem": "documento",
                "mensagem": f"Validade lida no documento: {validade:%d/%m/%Y}."}

    base = emissao or hoje
    base_txt = f"emitido em {base:%d/%m/%Y}" if emissao else f"emissão não encontrada, contei a partir de hoje ({base:%d/%m/%Y})"

    dias = _buscar_prazo_dias(t)
    if dias:
        v = base + timedelta(days=dias)
        return {"validade": v, "emissao": emissao, "origem": "prazo",
                "mensagem": f"Documento vale {dias} dias: {base_txt} → {v:%d/%m/%Y}."}

    v = base + timedelta(days=DIAS_PADRAO)
    return {"validade": v, "emissao": emissao, "origem": "padrao_90" if emissao else "padrao_90_envio",
            "mensagem": f"Sem validade no documento: {base_txt} + {DIAS_PADRAO} dias → {v:%d/%m/%Y}."}


def ler_validade(caminho, nome_original="", tipo="", hoje=None):
    hoje = hoje or date.today()
    if tipo in TIPOS_SEM_VALIDADE:
        return {"validade": None, "emissao": None, "origem": "nao_vence",
                "mensagem": "Este tipo de documento não tem validade."}
    texto = _texto_do_arquivo(caminho, nome_original)
    if texto is None or not texto.strip():
        v = hoje + timedelta(days=DIAS_PADRAO)
        motivo = "PDF escaneado (sem texto)" if texto is not None else "formato que não dá para ler"
        return {"validade": v, "emissao": None, "origem": "sem_texto",
                "mensagem": f"Não consegui ler o documento ({motivo}). Coloquei hoje + {DIAS_PADRAO} dias "
                            f"({v:%d/%m/%Y}). Confira a data."}
    return analisar_texto(texto, hoje)
