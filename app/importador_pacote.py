"""
Importacao em lote de licitacoes a partir do ZIP gerado pelo sistema de busca
de editais.

Formato esperado do ZIP:
  - uma pasta por processo, no padrao  <numero>_<uasg>_<orgao>
    (ex: "1852026_989403_PREFEITURA MUNICIPAL DE ITABERAI",
         "PCE 76_005004_MANUTENCAO DESENVOLVIMENTO E ENSINO",
         "10_MARIALVA CAMARA MUNICIPAL")
  - dentro de cada pasta, os arquivos do processo (edital, TR, anexos...)
  - na raiz, opcionalmente, um "resumo.xlsx" com as abas:
      Editais -> Numero do Edital | Itens | Objeto | UASG | Comprador |
                 Data de Publicacao | Data e Hora de Abertura |
                 Data e Hora de Disputa | Portal | Nome do Arquivo
      Itens   -> blocos por edital, com o Valor Total Estimado e a lista de
                 itens (Item, Grupo/Lote, Descricao, Unidade, Quantidade,
                 Valor Unitario...)

A planilha e a fonte principal dos dados. Quando ela nao existe (ou nao traz
algum campo), o importador tenta ler o proprio edital (PDF/DOCX/ODT).

Este modulo NAO grava nada no banco: ele so analisa o pacote e devolve uma
lista de "propostas" de licitacao, que a tela de conferencia mostra ao
assessor antes de criar de verdade.
"""
import os
import re
import shutil
import unicodedata
import zipfile
from datetime import datetime

from app.capag import UFS, UFS_VALIDAS, normalizar

# Limites de seguranca para o ZIP
LIMITE_DESCOMPACTADO = 2 * 1024 * 1024 * 1024  # 2 GB
LIMITE_ARQUIVOS = 5000

ARQUIVOS_IGNORADOS = {"thumbs.db", "desktop.ini", ".ds_store"}

# Nome da planilha (portal) -> opcao do Bidfy
MAPA_PORTAIS = {
    "comprasnet": "ComprasNet",
    "compras.gov.br": "ComprasNet",
    "compras gov": "ComprasNet",
    "portal de compras publicas": "PCP",
    "licitar digital": "Licitar Digital",
    "licitanet": "Licitanet",
    "procergs": "PROCERGS",
    "novo licitacoes-e2": "Banco do Brasil",
    "licitacoes-e": "Banco do Brasil",
    "banco do brasil": "Banco do Brasil",
    "bll": "BLL",
    "bnc": "BNC",
    "ammlicita": "AMMLICITA",
    "compras br": "Compras BR",
    "comprasbr": "Compras BR",
    "bbmnet": "BBMNET",
    "banrisul": "Banrisul",
}

# Palavras no texto do edital -> portal (usado quando a planilha diz so "PNCP")
PORTAIS_NO_TEXTO = [
    (r"licitardigital|licitar\.digital", "Licitar Digital"),
    (r"portaldecompraspublicas|portal de compras p[uú]blicas", "PCP"),
    (r"comprasnet|compras\.gov\.br|gov\.br/compras", "ComprasNet"),
    (r"bllcompras|bll\.org|\bbll\b", "BLL"),
    (r"licitanet", "Licitanet"),
    (r"bnccompras|bnc\.org", "BNC"),
    (r"licitacoes-e|licita[cç][oõ]es-e", "Banco do Brasil"),
    (r"pregaobanrisul|banrisul", "Banrisul"),
    (r"procergs|compras\.rs\.gov", "PROCERGS"),
    (r"ammlicita", "AMMLICITA"),
    (r"comprasbr|compras br\b", "Compras BR"),
    (r"bbmnet", "BBMNET"),
    (r"atende\.net", "Atende.net (IPM)"),
]

NOME_UF_NORMALIZADO = {normalizar(nome): sigla for sigla, nome in UFS}

PALAVRAS_FEDERAL = [
    "ministerio", "comando do exercito", "comando da marinha", "comando da aeronautica",
    "batalhao", "exercito", "marinha", "aeronautica", "universidade federal",
    "instituto federal", "tribunal regional", "justica federal", "receita federal",
    "policia federal", "policia rodoviaria federal", "agencia nacional",
    "fundacao nacional", "hospital universitario", "companhia de infantaria",
    "brigada", "regiao militar",
]
PALAVRAS_MUNICIPAL = [
    "municipio", "municipal", "prefeitura", "camara de vereadores",
    "servico autonomo", "saae", "dml/pm", "fundo municipal",
]
PALAVRAS_ESTADUAL = [
    "governo do estado", "secretaria de estado", "secretaria estadual",
    "assembleia legislativa", "tribunal de justica", "policia militar",
    "corpo de bombeiros", "detran",
]


# ─── utilidades ──────────────────────────────────────────────────────────────

def _sem_acento(txt):
    txt = unicodedata.normalize("NFKD", str(txt or ""))
    return "".join(c for c in txt if not unicodedata.combining(c))


def _titulo(txt):
    """'SANTA MARIA DO SALTO' -> 'Santa Maria do Salto'."""
    minusculas = {"de", "do", "da", "dos", "das", "e"}
    palavras = _limpar(txt).lower().split()
    return " ".join(p if (i and p in minusculas) else p.capitalize()
                    for i, p in enumerate(palavras))


def nome_amigavel(nome_arquivo):
    """Arquivos baixados do atende.net vem como
    '2Fvar_2Fwww_2Fhtml_2Fcidade.atende.net_2F...2FEdital_2Farquivo.pdf'.
    Deixa so o nome real do arquivo."""
    if "_2F" in nome_arquivo:
        return nome_arquivo.split("_2F")[-1] or nome_arquivo
    return nome_arquivo


def _limpar(txt):
    return " ".join(str(txt or "").split())


def _para_float(valor):
    if valor is None or valor == "":
        return None
    if isinstance(valor, (int, float)):
        return float(valor)
    s = str(valor).strip().replace("R$", "").strip()
    if "," in s:  # formato brasileiro 1.234,56
        s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _para_datetime(valor):
    if valor is None or valor == "":
        return None
    if isinstance(valor, datetime):
        return valor
    s = str(valor).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%d/%m/%Y %H:%M:%S",
                "%d/%m/%Y %H:%M", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


# ─── ZIP ─────────────────────────────────────────────────────────────────────

def _nome_zip(info):
    """Corrige nomes com acento de ZIPs gerados sem a flag UTF-8."""
    nome = info.filename
    if not (info.flag_bits & 0x800):
        try:
            nome = nome.encode("cp437").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    return nome


def extrair_zip(caminho_zip, destino):
    """Extrai o ZIP com protecao contra caminhos maliciosos e ZIP-bomba.
    Retorna a pasta que contem as pastas dos processos."""
    os.makedirs(destino, exist_ok=True)
    destino_abs = os.path.abspath(destino)
    with zipfile.ZipFile(caminho_zip) as z:
        infos = z.infolist()
        if len(infos) > LIMITE_ARQUIVOS:
            raise ValueError(f"O ZIP tem arquivos demais ({len(infos)}).")
        if sum(i.file_size for i in infos) > LIMITE_DESCOMPACTADO:
            raise ValueError("O ZIP descompactado passa de 2 GB.")
        for info in infos:
            nome = _nome_zip(info).replace("\\", "/")
            partes = [p for p in nome.split("/") if p not in ("", ".", "..")]
            if not partes or partes[0] == "__MACOSX":
                continue
            alvo = os.path.abspath(os.path.join(destino_abs, *partes))
            if not alvo.startswith(destino_abs + os.sep):
                continue
            if info.is_dir():
                os.makedirs(alvo, exist_ok=True)
                continue
            os.makedirs(os.path.dirname(alvo), exist_ok=True)
            with z.open(info) as origem, open(alvo, "wb") as saida:
                shutil.copyfileobj(origem, saida)

    # Se o ZIP tiver uma unica pasta "embrulhando" tudo, desce um nivel
    raiz = destino_abs
    for _ in range(2):
        itens = [i for i in os.listdir(raiz) if not i.startswith(".")]
        pastas = [i for i in itens if os.path.isdir(os.path.join(raiz, i))]
        arquivos = [i for i in itens if os.path.isfile(os.path.join(raiz, i))]
        if len(pastas) == 1 and not arquivos:
            raiz = os.path.join(raiz, pastas[0])
        else:
            break
    return raiz


# ─── resumo.xlsx ─────────────────────────────────────────────────────────────

def _achar_resumo(raiz):
    for nome in os.listdir(raiz):
        if nome.lower().endswith((".xlsx", ".xlsm")) and os.path.isfile(os.path.join(raiz, nome)):
            if "resumo" in nome.lower():
                return os.path.join(raiz, nome)
    # sem "resumo" no nome: aceita qualquer planilha solta na raiz que tenha aba Editais
    for nome in os.listdir(raiz):
        if nome.lower().endswith((".xlsx", ".xlsm")):
            return os.path.join(raiz, nome)
    return None


def _indices(cabecalho):
    return {normalizar(c): i for i, c in enumerate(cabecalho) if c is not None}


def _col(linha, idx, *nomes):
    for n in nomes:
        i = idx.get(n)
        if i is not None and i < len(linha):
            return linha[i]
    return None


def ler_resumo(caminho):
    """Le o resumo.xlsx. Retorna dict  nome_da_pasta(normalizado) -> dados."""
    from openpyxl import load_workbook

    wb = load_workbook(caminho, read_only=True, data_only=False)
    abas = {normalizar(ws.title): ws for ws in wb.worksheets}
    resultado = {}
    por_codigo = {}

    ws = abas.get("editais") or wb.worksheets[0]
    linhas = ws.iter_rows(values_only=True)
    cab = next(linhas, None)
    if not cab:
        return resultado
    idx = _indices(cab)
    for linha in linhas:
        pasta = _col(linha, idx, "nome do arquivo", "pasta")
        if not pasta:
            continue
        codigo = _limpar(_col(linha, idx, "numero do edital") or "")
        dados = {
            "codigo_busca": codigo,
            "objeto": _limpar(_col(linha, idx, "objeto") or ""),
            "uasg": _limpar(_col(linha, idx, "uasg") or ""),
            "comprador": _limpar(_col(linha, idx, "comprador", "orgao") or ""),
            "data_publicacao": _para_datetime(_col(linha, idx, "data de publicacao")),
            "data_abertura": _para_datetime(_col(linha, idx, "data e hora de abertura")),
            "data_disputa": _para_datetime(_col(linha, idx, "data e hora de disputa")),
            "portal": _limpar(_col(linha, idx, "portal") or ""),
            "valor_estimado": None,
            "itens": [],
        }
        resultado[normalizar(pasta)] = dados
        if codigo:
            por_codigo[codigo] = dados

    ws_itens = abas.get("itens")
    if ws_itens is not None:
        _ler_aba_itens(ws_itens, resultado, por_codigo)
    wb.close()
    return resultado


def _ler_aba_itens(ws, por_pasta, por_codigo):
    """A aba Itens tem um bloco por edital:
       titulo / cabecalho do edital / dados do edital / cabecalho dos itens /
       itens... / 'Total estimado:'"""
    atual = None
    idx_edital = None
    idx_itens = None
    esperando = None  # "dados_edital" | "itens"
    for linha in ws.iter_rows(values_only=True):
        valores = [v for v in linha if v not in (None, "")]
        if not valores:
            continue
        primeira = normalizar(linha[0]) if linha[0] is not None else ""

        if primeira == "numero do edital":
            idx_edital = _indices(linha)
            esperando = "dados_edital"
            atual = None
            continue
        if esperando == "dados_edital" and idx_edital is not None:
            pasta = normalizar(_col(linha, idx_edital, "nome do arquivo") or "")
            codigo = _limpar(_col(linha, idx_edital, "numero do edital") or "")
            atual = por_pasta.get(pasta) or por_codigo.get(codigo)
            if atual is not None:
                v = _para_float(_col(linha, idx_edital, "valor total estimado"))
                if v:
                    atual["valor_estimado"] = round(v, 2)
            esperando = None
            continue
        if primeira == "item" and "descricao" in [normalizar(c) for c in linha if c]:
            idx_itens = _indices(linha)
            esperando = "itens"
            continue
        if any(normalizar(v).startswith("total estimado") for v in valores if isinstance(v, str)):
            esperando = None
            continue
        if esperando == "itens" and atual is not None and idx_itens is not None:
            descricao = _limpar(_col(linha, idx_itens, "descricao") or "")
            detalhe = _limpar(_col(linha, idx_itens, "detalhamento") or "")
            if not descricao and not detalhe:
                continue
            if len(detalhe) > len(descricao):
                descricao = detalhe
            qtd = _para_float(_col(linha, idx_itens, "quantidade"))
            atual["itens"].append({
                "numero_item": _limpar(_col(linha, idx_itens, "item") or "")[:20] or None,
                "lote_grupo": _limpar(_col(linha, idx_itens, "grupo / lote", "grupo/lote", "lote", "grupo") or "")[:100] or None,
                "descricao": descricao[:500],
                "unidade": _limpar(_col(linha, idx_itens, "unidade") or "")[:50] or None,
                "quantidade": int(round(qtd)) if qtd is not None else None,
                "valor_estimado": _para_float(_col(linha, idx_itens, "valor unitario")),
            })


# ─── leitura do edital ───────────────────────────────────────────────────────

EXT_TEXTO = (".pdf", ".docx", ".odt", ".txt")


def _pontuar_edital(nome):
    n = normalizar(os.path.splitext(nome)[0]).replace("_", " ").replace("-", " ")
    ext = os.path.splitext(nome)[1].lower()
    if ext not in EXT_TEXTO:
        return -100
    p = 0
    if "edital" in n:
        p += 10
    if re.search(r"\b(pregao|pe ?\d|pe\d|\d+ ?edi$|1edi)\b", n) or re.search(r"^pe ?\d", n) or n.endswith("edi"):
        p += 6
    if "retific" in n or "corrigid" in n:
        p += 3
    if "minuta" in n:
        p -= 6
    if "anexo" in n and "edital e seus anexos" not in n:
        p -= 5
    for ruim in ("aviso", "publicacao", "comprovante", "julgamento", "impugna",
                 "pedido", "parecer", "decreto", "oficio", "planilha", "etp",
                 "estudo tecnico", "termo de referencia", "contrato", "ata ",
                 "dfd", "orcamento", "cotacao", "pesquisa de preco", "nota para"):
        if ruim in n + " ":
            p -= 8
    if re.search(r"\btr\b", n):
        p -= 8
    if ext == ".pdf":
        p += 1
    return p


def escolher_edital(arquivos):
    """Recebe [(nome, caminho, tamanho)] e devolve o indice do edital (ou None)."""
    if not arquivos:
        return None
    melhores = sorted(range(len(arquivos)),
                      key=lambda i: (_pontuar_edital(arquivos[i][0]), arquivos[i][2]),
                      reverse=True)
    i = melhores[0]
    if _pontuar_edital(arquivos[i][0]) >= 6:
        return i
    # Nenhum nome claro: entre os documentos que nao parecem anexo, fica com o
    # que tem mais texto nas primeiras paginas (evita pegar PDF escaneado/planilha)
    candidatos = [j for j in melhores
                  if arquivos[j][0].lower().endswith((".pdf", ".docx", ".odt"))
                  and _pontuar_edital(arquivos[j][0]) >= 0][:6]
    if not candidatos:
        return None
    if len(candidatos) == 1:
        return candidatos[0]
    return max(candidatos, key=lambda j: (len(texto_inicio(arquivos[j][1], 6000).strip()), arquivos[j][2]))


def escolher_termo_referencia(arquivos, idx_edital):
    melhor, pontos_melhor = None, 0
    for i, (nome, _c, _t) in enumerate(arquivos):
        if i == idx_edital:
            continue
        n = normalizar(os.path.splitext(nome)[0]).replace("_", " ").replace("-", " ")
        p = 0
        if "termo de referencia" in n or "termo de ref" in n:
            p = 10
        elif re.search(r"\btr\b", n) or n.endswith("2pbt") or "projeto basico" in n:
            p = 7
        if p and ("retific" in n):
            p += 2
        if p and "apendice" in n:
            p -= 4
        if p > pontos_melhor:
            melhor, pontos_melhor = i, p
    return melhor


def _pdftotext(caminho, paginas=3):
    """Usa o pdftotext (poppler) se estiver instalado: e bem mais rapido que o
    pypdf em PDFs pesados. Retorna None se o programa nao existir."""
    import subprocess
    if not shutil.which("pdftotext"):
        return None
    try:
        r = subprocess.run(["pdftotext", "-q", "-f", "1", "-l", str(paginas), caminho, "-"],
                           capture_output=True, timeout=20)
        return r.stdout.decode("utf-8", errors="ignore")
    except Exception:
        return ""


def texto_inicio(caminho, max_chars=12000):
    """Texto das primeiras paginas do documento (PDF, DOCX, ODT, TXT)."""
    ext = os.path.splitext(caminho)[1].lower()
    try:
        if ext == ".pdf":
            texto = _pdftotext(caminho)
            if texto is not None:
                return texto[:max_chars]
            from pypdf import PdfReader
            leitor = PdfReader(caminho)
            partes = []
            for pagina in leitor.pages[:4]:
                partes.append(pagina.extract_text() or "")
                if sum(len(p) for p in partes) > max_chars:
                    break
            return "\n".join(partes)[:max_chars]
        if ext == ".docx":
            from docx import Document
            doc = Document(caminho)
            partes, total = [], 0
            for p in doc.paragraphs:
                if p.text.strip():
                    partes.append(p.text)
                    total += len(p.text)
                    if total > max_chars:
                        break
            if total < 300:  # cabecalho em tabela
                for t in doc.tables[:3]:
                    for linha in t.rows:
                        partes.append(" ".join(c.text for c in linha.cells))
            return "\n".join(partes)[:max_chars]
        if ext == ".odt":
            with zipfile.ZipFile(caminho) as z:
                xml = z.read("content.xml").decode("utf-8", errors="ignore")
            xml = re.sub(r"</text:(p|h)>", "\n", xml)
            xml = re.sub(r"<[^>]+>", "", xml)
            import html
            return html.unescape(xml)[:max_chars]
        if ext == ".txt":
            with open(caminho, encoding="utf-8", errors="ignore") as f:
                return f.read(max_chars)
    except Exception:
        return ""
    return ""


RE_ENTE = re.compile(
    r"(PREFEITURA\s+MUNICIPAL\s+DE|PREFEITURA\s+DE|MUNIC[IÍ]PIO\s+DE|"
    r"C[AÂ]MARA\s+MUNICIPAL\s+DE)\s+([A-ZÀ-Üa-zà-ü'´`\s\-]{3,60})",
    re.IGNORECASE,
)
PARADAS_ENTE = re.compile(
    r"\s(estado|torna|inscrit|cnpj|pessoa|atraves|por meio|leva|realiza|com sede|"
    r"situad|neste|nesta|para|secretaria|departamento|setor|av|rua|fone|tel|e-mail|"
    r"processo|pregao|edital|licitac|cep)\b.*$",
    re.IGNORECASE,
)


def _candidatos_ente(texto):
    """Nomes de municipio citados no inicio do edital (ordem de aparicao)."""
    nomes = []
    for m in RE_ENTE.finditer(texto[:6000]):
        bruto = m.group(2).split("\n")[0]
        bruto = re.split(r"[,;:.()/–]|\s-\s|-\s|\s-", bruto)[0]
        bruto = PARADAS_ENTE.sub("", " " + bruto).strip(" -")
        bruto = _limpar(bruto)
        if 3 <= len(bruto) <= 50 and normalizar(bruto) not in ("lei", "contratacao"):
            nomes.append(bruto)
    return nomes


def detectar_uf(*textos):
    """Procura a UF em textos livres (comprador, nome da pasta, edital)."""
    for t in textos:
        if not t:
            continue
        s = _limpar(t)
        # sufixo "/MT", "- SC", " RS", "-MG"
        m = re.search(r"(?:/|\s-\s|-|\s)([A-Z]{2})\)?\s*$", s)
        if m and m.group(1) in UFS_VALIDAS:
            return m.group(1)
    for t in textos:
        if not t:
            continue
        n = normalizar(t)
        m = re.search(r"estado d[eoa]s?\s+([a-z\s]{4,25})", n)
        if m:
            trecho = m.group(1)
            for nome, sigla in sorted(NOME_UF_NORMALIZADO.items(), key=lambda x: -len(x[0])):
                if trecho.startswith(nome):
                    return sigla
        m = re.search(r"\.([a-z]{2})\.gov\.br", n)
        if m and m.group(1).upper() in UFS_VALIDAS:
            return m.group(1).upper()
        m = re.search(r"[a-z]\s?[-/]\s?([a-z]{2})\b[\s.,)]", n)
        if m and m.group(1).upper() in UFS_VALIDAS and m.group(1) not in ("de", "do", "da", "se", "ao", "no", "na", "em", "es"):
            # "es" e ambiguo (Espirito Santo x palavra) -> so aceita via sufixo/estado
            return m.group(1).upper()
    return None


def inferir_esfera(texto_orgao):
    n = normalizar(texto_orgao)
    if "consorcio" in n:
        return "consorcio"
    if any(p in n for p in PALAVRAS_MUNICIPAL):
        return "municipal"
    if any(p in n for p in PALAVRAS_FEDERAL):
        return "federal"
    if any(p in n for p in PALAVRAS_ESTADUAL):
        return "estadual"
    return None


class BaseMunicipios:
    """Lista de municipios (da base CAPAG) para reconhecer nomes no texto."""

    def __init__(self, registros):
        # registros: [(nome, uf, nome_normalizado)]
        self.por_nome = {}
        for nome, uf, norm in registros:
            self.por_nome.setdefault(norm or normalizar(nome), []).append((nome, uf))
        self.nomes_ordenados = sorted(self.por_nome, key=len, reverse=True)

    def achar(self, texto, uf=None, exigir_inicio=False):
        """Acha o municipio (nome, uf) citado no texto. Com exigir_inicio, o
        nome tem que estar no comeco do texto (usado nos candidatos do edital)."""
        alvo = " " + " ".join(re.sub(r"[^a-z0-9]+", " ", normalizar(texto)).split()) + " "
        for norm in self.nomes_ordenados:
            if len(norm) < 4:
                continue
            chave = " " + norm + " "
            if exigir_inicio:
                if not alvo.startswith(chave):
                    continue
            elif chave not in alvo:
                continue
            opcoes = self.por_nome[norm]
            if uf:
                opcoes_uf = [o for o in opcoes if o[1] == uf]
                if opcoes_uf:
                    return opcoes_uf[0]
                continue
            if len(opcoes) == 1:
                return opcoes[0]
        return None


def detectar_portal_no_texto(texto):
    n = _sem_acento(texto).lower()
    for padrao, portal in PORTAIS_NO_TEXTO:
        if re.search(padrao, n):
            return portal
    return None


def mapear_portal(nome):
    n = normalizar(nome)
    if not n:
        return None
    for chave, valor in MAPA_PORTAIS.items():
        if chave in n:
            return valor
    return None


RE_VALOR = re.compile(
    r"valor\s+(?:total\s+|global\s+|m[aá]ximo\s+)*(?:estimado|de\s+refer[eê]ncia)[^R]{0,80}R\$\s*([\d.]+,\d{2})",
    re.IGNORECASE,
)
RE_DATA_HORA = re.compile(
    r"(\d{1,2})[/.](\d{1,2})[/.](20\d\d)[^\d\n]{0,25}?(\d{1,2})\s*[h:]\s*(\d{2})",
    re.IGNORECASE,
)


def valor_no_texto(texto):
    m = RE_VALOR.search(texto)
    return _para_float(m.group(1)) if m else None


def data_no_texto(texto):
    """Primeira data+hora perto de 'sessao'/'disputa'/'abertura'."""
    n = texto
    for chave in ("sess", "disputa", "abertura", "lances"):
        for m in re.finditer(chave, n, re.IGNORECASE):
            trecho = n[m.start(): m.start() + 300]
            d = RE_DATA_HORA.search(trecho)
            if d:
                try:
                    return datetime(int(d.group(3)), int(d.group(2)), int(d.group(1)),
                                    int(d.group(4)), int(d.group(5)))
                except ValueError:
                    continue
    return None


# ─── nome da pasta ───────────────────────────────────────────────────────────

def parse_nome_pasta(nome):
    """'1852026_989403_PREFEITURA X' -> ('1852026', '989403', 'PREFEITURA X')
       '10_MARIALVA CAMARA MUNICIPAL' -> ('10', None, 'MARIALVA CAMARA MUNICIPAL')"""
    partes = nome.split("_")
    numero_bruto = partes[0].strip() if partes else ""
    uasg, orgao = None, ""
    if len(partes) >= 3 and re.fullmatch(r"\d{1,20}", partes[1].strip()):
        uasg = partes[1].strip()
        orgao = "_".join(partes[2:])
    else:
        orgao = "_".join(partes[1:])
    return numero_bruto, uasg, _limpar(orgao)


def formatar_numero(numero_bruto, ano_ref):
    """'1852026' -> '185/2026' ; 'PCE 76' -> '76/2026' ; '0068' -> '68/2026'."""
    digitos = re.sub(r"\D", "", numero_bruto or "")
    if not digitos:
        return (numero_bruto or "").strip() or "s/n"
    anos = {str(a) for a in range((ano_ref or datetime.now().year) - 1,
                                  (ano_ref or datetime.now().year) + 2)}
    if len(digitos) > 4 and digitos[-4:] in anos:
        num, ano = digitos[:-4], digitos[-4:]
    else:
        num, ano = digitos, str(ano_ref or datetime.now().year)
    num = num.lstrip("0") or "0"
    return f"{num}/{ano}"


# ─── analise do pacote ───────────────────────────────────────────────────────

def _listar_arquivos(pasta):
    arquivos = []
    for raiz, _dirs, nomes in os.walk(pasta):
        for nome in sorted(nomes):
            if nome.lower() in ARQUIVOS_IGNORADOS or nome.startswith("~$"):
                continue
            caminho = os.path.join(raiz, nome)
            rel = os.path.relpath(caminho, pasta)
            arquivos.append((rel.replace(os.sep, " / "), caminho, os.path.getsize(caminho)))
    return arquivos


def analisar_pacote(raiz, municipios=None, agora=None):
    """Analisa a pasta extraida e devolve a lista de propostas de licitacao.

    municipios: BaseMunicipios (opcional) para reconhecer o municipio/CAPAG.
    """
    agora = agora or datetime.now()
    caminho_resumo = _achar_resumo(raiz)
    resumo = {}
    aviso_geral = []
    if caminho_resumo:
        try:
            resumo = ler_resumo(caminho_resumo)
        except Exception as e:
            aviso_geral.append(f"Não consegui ler a planilha {os.path.basename(caminho_resumo)}: {e}")
    else:
        aviso_geral.append("O pacote não tem a planilha resumo.xlsx — os dados foram lidos dos editais (confira com atenção).")

    propostas = []
    pastas = sorted(p for p in os.listdir(raiz)
                    if os.path.isdir(os.path.join(raiz, p)) and not p.startswith((".", "__")))
    for nome_pasta in pastas:
        caminho_pasta = os.path.join(raiz, nome_pasta)
        propostas.append(_analisar_pasta(nome_pasta, caminho_pasta,
                                         resumo.get(normalizar(nome_pasta)),
                                         municipios, agora))

    _marcar_duplicadas_no_pacote(propostas)
    return {"propostas": propostas, "avisos": aviso_geral,
            "tem_resumo": bool(resumo)}


def _analisar_pasta(nome_pasta, caminho_pasta, info, municipios, agora):
    avisos = []
    info = info or {}
    numero_bruto, uasg_pasta, orgao_pasta = parse_nome_pasta(nome_pasta)
    arquivos = _listar_arquivos(caminho_pasta)

    i_edital = escolher_edital(arquivos)
    i_tr = escolher_termo_referencia(arquivos, i_edital)
    texto = texto_inicio(arquivos[i_edital][1]) if i_edital is not None else ""
    if i_edital is None:
        avisos.append("Edital não identificado entre os arquivos.")
    elif len(texto.strip()) < 100 and not info:
        avisos.append("Edital sem texto legível (PDF escaneado?).")
    if any(a[0].lower().endswith((".rar", ".7z")) for a in arquivos):
        avisos.append("Tem arquivo .rar/.7z — extraia manualmente se precisar do conteúdo.")

    # Datas
    data_disputa = info.get("data_disputa") or data_no_texto(texto)
    if not data_disputa:
        avisos.append("Data da disputa não encontrada.")
    ano_ref = (data_disputa or info.get("data_publicacao") or agora).year

    # Orgao
    comprador = info.get("comprador") or orgao_pasta
    comprador_n = normalizar(comprador)
    generico = comprador_n in ("unidade unica", "") or len(comprador_n) < 4

    # Esfera / UF / municipio
    esfera = inferir_esfera(comprador) if not generico else None
    candidatos = _candidatos_ente(texto)
    uf = detectar_uf(comprador, orgao_pasta, texto[:4000])
    municipio = None
    if municipios and esfera in (None, "municipal"):
        achado = None
        if not generico:
            # 1o: nome logo depois de "Municipio de"/"Prefeitura de" no comprador
            for cand in _candidatos_ente(comprador):
                achado = municipios.achar(cand, uf, exigir_inicio=True)
                if achado:
                    break
            if not achado:
                achado = municipios.achar(comprador, uf)
        if not achado:
            for cand in candidatos:
                achado = municipios.achar(cand, uf, exigir_inicio=True)
                if achado:
                    break
        if achado:
            municipio, uf_mun = achado
            uf = uf or uf_mun
            esfera = esfera or "municipal"
        elif generico and candidatos:
            esfera = "municipal"
    elif not municipios and esfera in (None, "municipal") and candidatos:
        municipio = _titulo(candidatos[0])
        esfera = esfera or "municipal"
    if esfera == "federal":
        municipio, uf = None, None
    elif esfera == "estadual":
        municipio = None

    # Nome do orgao que aparece no Bidfy
    if generico and municipio:
        orgao = f"Município de {municipio}"
    elif generico and candidatos:
        orgao = f"Município de {_titulo(candidatos[0])}"
    elif generico:
        orgao = comprador or orgao_pasta or nome_pasta
        avisos.append("Órgão não identificado (a planilha diz só \"Unidade Única\").")
    else:
        orgao = comprador
        if municipio and normalizar(municipio) not in comprador_n:
            orgao = f"{comprador} — Município de {municipio}"
    if uf and municipio and f"/{uf}" not in orgao and f" {uf}" not in orgao[-4:]:
        orgao = f"{orgao}/{uf}" if not orgao.endswith(uf) else orgao

    # Portal
    portal_planilha = info.get("portal") or ""
    portal = mapear_portal(portal_planilha)
    if not portal:
        portal = detectar_portal_no_texto(texto)
    if not portal:
        portal = "PNCP" if normalizar(portal_planilha) == "pncp" else (portal_planilha or None)
        avisos.append("Portal da disputa não identificado — confira no edital.")

    # Valor estimado
    valor = info.get("valor_estimado") or valor_no_texto(texto)

    # UASG (so faz sentido curta; codigos gigantes de portal sao descartados)
    uasg = info.get("uasg") or uasg_pasta or ""
    if not re.fullmatch(r"\d{1,9}", uasg or ""):
        uasg = ""

    objeto = info.get("objeto") or ""
    objeto = re.sub(r"^\[[^\]]+\]\s*-\s*", "", objeto)  # tira "[Portal de Compras Publicas] - "

    situacao_data = None
    if data_disputa and data_disputa < agora:
        situacao_data = "passada"
        avisos.append("A data da disputa já passou.")

    arquivos_saida = []
    for i, (nome, caminho, tamanho) in enumerate(arquivos):
        tipo = "edital" if i == i_edital else ("termo_referencia" if i == i_tr else "outros")
        arquivos_saida.append({"nome": nome_amigavel(nome), "caminho": caminho, "tamanho": tamanho, "tipo": tipo})

    return {
        "pasta": nome_pasta,
        "codigo_busca": info.get("codigo_busca") or "",
        "numero_pregao": formatar_numero(numero_bruto, ano_ref),
        "uasg": uasg,
        "orgao_licitante": orgao[:300],
        "objeto": objeto,
        "portal": portal,
        "data_disputa": data_disputa.strftime("%Y-%m-%dT%H:%M") if data_disputa else None,
        "valor_estimado": round(valor, 2) if valor else None,
        "esfera": esfera,
        "uf": uf if esfera in ("municipal", "estadual") else None,
        "municipio": municipio if esfera == "municipal" else None,
        "itens": info.get("itens") or [],
        "arquivos": arquivos_saida,
        "avisos": avisos,
        "situacao_data": situacao_data,
        "duplicada_de": None,
        "selecionada": situacao_data != "passada",
    }


def _marcar_duplicadas_no_pacote(propostas):
    """O mesmo processo pode vir duas vezes (ex: achado no PNCP e no portal).
    Marca a segunda ocorrencia (mesma data de disputa + mesmo numero + mesma UF)."""
    vistos = {}
    for p in propostas:
        num = (p["numero_pregao"] or "").split("/")[0]
        chave = (p["data_disputa"], num, normalizar(p.get("municipio") or ""))
        if not p["data_disputa"]:
            continue
        if chave in vistos:
            p["duplicada_de"] = vistos[chave]
            p["avisos"].append(f"Parece repetida da pasta \"{vistos[chave]}\".")
            p["selecionada"] = False
        else:
            vistos[chave] = p["pasta"]
