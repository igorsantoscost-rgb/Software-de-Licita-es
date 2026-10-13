"""
Conferencias dos documentos de habilitacao do cliente, sem IA.

- CNPJ: o documento da empresa precisa citar o CNPJ do cliente
  (mesma raiz = mesma empresa; final diferente = filial).
- Socio: o documento de identificacao precisa trazer o CPF (ou o nome) do socio.
- Contrato social: encontra os socios (nome, CPF e estado civil) para
  montar o quadro societario.

Cada conferencia devolve {"nivel": "ok" | "aviso" | "erro" | "", "mensagem": "..."}.
"erro" = documento parece ser de outra empresa/pessoa: so salva com confirmacao.
"""
import os
import re
import unicodedata

# Documentos do quadro societario (um conjunto por socio)
TIPOS_SOCIO = ("doc_socio", "estado_civil", "doc_conjuge")
# Documentos que pertencem a empresa e devem citar o CNPJ do cliente
TIPOS_SEM_CNPJ = set(TIPOS_SOCIO) | {"outros_documentos"}
# Contrato/alteracao podem nao citar o CNPJ (constituicao e anterior a ele)
TIPOS_CNPJ_OPCIONAL = {"contrato_social", "alteracao_contratual", "atestado_tecnico",
                       "contrato_consultoria", "aditivo_consultoria"}

RE_CNPJ = re.compile(r"(?<![\d.])(\d{2})\.?(\d{3})\.?(\d{3})\s*/\s*(\d{4})\s*-?\s*(\d{2})(?!\d)")
RE_CNPJ_RAIZ = re.compile(r"(?<![\d.])(\d{2})\.(\d{3})\.(\d{3})(?![\d/.])")
RE_CPF = re.compile(r"(?<![\d./])(\d{3})\.?(\d{3})\.?(\d{3})\s*-\s*(\d{2})(?![\d/])|(?<![\d./])(\d{3})\.(\d{3})\.(\d{3})(\d{2})(?![\d/])")
RE_CPF_SOLTO = re.compile(r"(?<!\d)(\d{11})(?!\d)")

ESTADOS_CIVIS = [
    ("uniao estavel", "união estável"),
    ("convivente", "união estável"),
    ("casad", "casado(a)"),
    ("solteir", "solteiro(a)"),
    ("divorciad", "divorciado(a)"),
    ("separad", "separado(a)"),
    ("viuv", "viúvo(a)"),
]


def so_digitos(txt):
    return re.sub(r"\D", "", txt or "")


def formatar_cnpj(d):
    d = so_digitos(d)
    return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}" if len(d) == 14 else d


def formatar_cpf(d):
    d = so_digitos(d)
    return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}" if len(d) == 11 else d


def _dv_ok(base, pesos_list):
    for pesos in pesos_list:
        s = sum(int(n) * p for n, p in zip(base, pesos))
        r = s % 11
        base += "0" if r < 2 else str(11 - r)
    return base


def cnpj_valido(d):
    d = so_digitos(d)
    if len(d) != 14 or d == d[0] * 14:
        return False
    p1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    p2 = [6] + p1
    return _dv_ok(d[:12], [p1, p2]) == d


def cpf_valido(d):
    d = so_digitos(d)
    if len(d) != 11 or d == d[0] * 11:
        return False
    return _dv_ok(d[:9], [list(range(10, 1, -1)), list(range(11, 1, -1))]) == d


def normalizar(txt):
    t = unicodedata.normalize("NFKD", txt or "")
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", t.lower()).strip()


def texto_do_arquivo(caminho, nome_original=""):
    """Texto do documento. None = formato que nao da para ler; "" = sem texto (escaneado)."""
    ext = os.path.splitext(nome_original or caminho)[1].lower()
    try:
        if ext == ".pdf":
            from app.proposta_pdf import texto_do_pdf
            return texto_do_pdf(caminho) or ""
        if ext == ".docx":
            from app.doc_extractor import extrair_texto_docx
            return extrair_texto_docx(caminho) or ""
        if ext in (".txt", ".html", ".htm"):
            with open(caminho, encoding="utf-8", errors="ignore") as f:
                return f.read()
    except Exception:
        return ""
    return None


def tem_texto(texto):
    return bool(texto and len(re.sub(r"\s", "", texto)) >= 20)


# ─── CNPJ ────────────────────────────────────────────────────────────────────

def cnpjs_do_texto(texto):
    completos = []
    for m in RE_CNPJ.finditer(texto or ""):
        d = "".join(m.groups())
        if cnpj_valido(d) and d not in completos:
            completos.append(d)
    raizes = []
    for m in RE_CNPJ_RAIZ.finditer(texto or ""):
        r = "".join(m.groups())
        if r not in raizes:
            raizes.append(r)
    return completos, raizes


def conferir_cnpj(texto, cnpj_cliente, tipo):
    if tipo in TIPOS_SEM_CNPJ:
        return {"nivel": "", "mensagem": ""}
    cli = so_digitos(cnpj_cliente)
    if len(cli) != 14:
        return {"nivel": "aviso", "mensagem": "O cliente está sem CNPJ no cadastro, então não deu para conferir o documento."}
    if texto is None or not tem_texto(texto):
        return {"nivel": "aviso", "mensagem": "Documento escaneado ou em imagem: não deu para conferir o CNPJ. Confira a olho."}

    completos, raizes = cnpjs_do_texto(texto)
    if cli in completos:
        return {"nivel": "ok", "mensagem": f"CNPJ confere com o cliente ({formatar_cnpj(cli)})."}
    mesma_raiz = [c for c in completos if c[:8] == cli[:8]]
    if mesma_raiz:
        return {"nivel": "aviso",
                "mensagem": f"O documento é de outro estabelecimento da mesma empresa ({formatar_cnpj(mesma_raiz[0])}). "
                            f"O cadastro do cliente é {formatar_cnpj(cli)}."}
    if cli[:8] in raizes:
        return {"nivel": "ok", "mensagem": f"CNPJ confere com o cliente (raiz {formatar_cnpj(cli)[:10]})."}
    if completos:
        lista = ", ".join(formatar_cnpj(c) for c in completos[:3])
        return {"nivel": "erro",
                "mensagem": f"O CNPJ do documento ({lista}) é diferente do CNPJ do cliente ({formatar_cnpj(cli)}). "
                            f"Este documento parece ser de outra empresa."}
    if tipo in TIPOS_CNPJ_OPCIONAL:
        return {"nivel": "", "mensagem": ""}
    return {"nivel": "aviso", "mensagem": "Não encontrei nenhum CNPJ no documento. Confira se ele é mesmo do cliente."}


# ─── Socio ───────────────────────────────────────────────────────────────────

def cpfs_do_texto(texto):
    achados = []
    for m in RE_CPF.finditer(texto or ""):
        d = "".join(g for g in m.groups() if g)
        if cpf_valido(d) and d not in achados:
            achados.append(d)
    for m in RE_CPF_SOLTO.finditer(texto or ""):
        d = m.group(1)
        if cpf_valido(d) and d not in achados:
            achados.append(d)
    return achados


def _nome_no_texto(nome, texto_norm):
    partes = [p for p in normalizar(nome).split() if len(p) > 2 and p not in ("dos", "das", "de", "da", "do")]
    if len(partes) < 2:
        return bool(partes) and partes[0] in texto_norm
    return partes[0] in texto_norm and partes[-1] in texto_norm


def conferir_socio(texto, socio, tipo):
    """Confere se o documento e do socio certo (CPF ou nome)."""
    if socio is None or tipo == "doc_conjuge":
        return {"nivel": "", "mensagem": ""}
    if texto is None or not tem_texto(texto):
        return {"nivel": "", "mensagem": ""}  # RG/CNH em foto: sem como conferir
    t = normalizar(texto)
    cpf = so_digitos(socio.cpf)
    cpfs = cpfs_do_texto(texto)
    nome_ok = _nome_no_texto(socio.nome, t)

    if len(cpf) == 11 and cpf in cpfs:
        return {"nivel": "ok", "mensagem": f"CPF confere com o sócio {socio.nome}."}
    if tipo == "doc_socio" and len(cpf) == 11 and cpfs and not nome_ok:
        lista = ", ".join(formatar_cpf(c) for c in cpfs[:2])
        return {"nivel": "erro",
                "mensagem": f"O CPF do documento ({lista}) é diferente do CPF do sócio {socio.nome} "
                            f"({formatar_cpf(cpf)}). Este documento parece ser de outra pessoa."}
    if nome_ok:
        return {"nivel": "ok", "mensagem": f"Nome do sócio {socio.nome} encontrado no documento."}
    return {"nivel": "aviso", "mensagem": f"Não encontrei o nome nem o CPF de {socio.nome} no documento. Confira se é dele."}


# ─── Socios do contrato social ───────────────────────────────────────────────

_NACIONALIDADE = r"(?:brasileir[oa]|portugu[eê]s[a]?|argentin[oa]|estrangeir[oa]|naturalizad[oa]|de nacionalidade|nacionalidade)"
_PALAVRA_NOME = r"[A-ZÀ-Ý][A-Za-zÀ-ÿ'´`.]+"
_CONECTIVO = r"(?:d[aeo]s?|e|D[AEO]S?|E)"
RE_PESSOA = re.compile(
    r"(" + _PALAVRA_NOME + r"(?:\s+(?:" + _CONECTIVO + r"|" + _PALAVRA_NOME + r")){1,9})\s*,\s*" + _NACIONALIDADE,
)
_ANTES_NAO_SOCIO = re.compile(r"((casad[oa]|uni[aã]o\s+est[aá]vel|convivente)[^;]{0,90}?\bcom|c[oô]njuge|espos[oa]|marido|"
                              r"companheir[oa]|representad[oa]\s+por|procurador[a]?|assistid[oa]\s+por|"
                              r"contador[a]?|advogad[oa]|testemunhas?)\s*:?\s*(?:a\s+|o\s+|sr[a]?\.?\s+)?$", re.IGNORECASE)


_CONECTIVOS_NOME = {"de", "da", "do", "dos", "das", "e"}


def _limpa_nome(nome):
    nome = re.sub(r"^(?:(?:o|a|os|as|sr\.?|sra\.?|s[oó]ci[oa]s?|cotista|e)\s+)+", "", nome.strip(), flags=re.IGNORECASE)
    nome = re.sub(r"\s+", " ", nome).strip(" .,;:")
    if nome.isupper() or nome.islower():
        partes = nome.lower().split()
        nome = " ".join(p if (i and p in _CONECTIVOS_NOME) else p.capitalize() for i, p in enumerate(partes))
    return nome


def socios_do_contrato(texto):
    """Lista de {"nome", "cpf", "estado_civil"} encontrada no contrato social.

    Le o texto em ordem: cada pessoa qualificada ("NOME, brasileiro, ...") entra
    numa pilha e cada CPF pertence a ultima pessoa da pilha que ainda nao teve CPF.
    Assim o conjuge citado dentro da qualificacao do socio ("casado com MARIA...,
    CPF ...") fica com o CPF dele e o socio com o seu. Conjuge, procurador e
    testemunha nao entram na lista.
    """
    if not tem_texto(texto):
        return []
    plano = re.sub(r"\s+", " ", texto.replace("\r", " "))
    eventos = []
    for m in RE_PESSOA.finditer(plano):
        antes = plano[max(0, m.start() - 110):m.start()]
        eventos.append((m.start(), "pessoa", m, bool(_ANTES_NAO_SOCIO.search(antes))))
    for m in RE_CPF.finditer(plano):
        cpf = "".join(g for g in m.groups() if g)
        if cpf_valido(cpf):
            eventos.append((m.start(), "cpf", m, cpf))
    eventos.sort(key=lambda e: e[0])

    pilha, socios, vistos = [], [], set()
    for pos, tipo, m, extra in eventos:
        if tipo == "pessoa":
            pilha.append((m, extra))
            continue
        cpf = extra
        # Nome citado muito antes do CPF nao e o dono dele
        pilha = [(pm, ns) for pm, ns in pilha if pos - pm.end() <= 900]
        if not pilha:
            continue
        pessoa, nao_socio = pilha.pop()
        if cpf in vistos:
            continue
        vistos.add(cpf)
        if nao_socio:
            continue
        nome = _limpa_nome(pessoa.group(1))
        if len(nome.split()) < 2:
            continue
        qualificacao = normalizar(plano[pessoa.end():pos])
        estado = ""
        for chave, rotulo in ESTADOS_CIVIS:
            if chave in qualificacao[:250]:
                estado = rotulo
                break
        socios.append({"nome": nome, "cpf": formatar_cpf(cpf), "estado_civil": estado})
    return socios
