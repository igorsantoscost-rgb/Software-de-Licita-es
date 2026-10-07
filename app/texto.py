"""
Padronizacao de textos que chegam de fora (pacote de editais / sistema de
busca), quase sempre TODO EM MAIUSCULO.

- nomes (orgao, municipio): "PREFEITURA MUNICIPAL DE ITABERAI/GO"
      -> "Prefeitura Municipal de Itaberai/GO"
- textos corridos (objeto, itens): "AQUISICAO DE TINTA ACRILICA PVC 18L"
      -> "Aquisicao de tinta acrilica PVC 18L"

Textos digitados pelo operador nao passam por aqui.
"""
import re

from app.capag import UFS_VALIDAS

# Palavras que ficam minusculas no meio de um nome
CONECTIVOS = {"de", "do", "da", "dos", "das", "e", "em", "no", "na", "nos", "nas",
              "a", "o", "as", "os", "para", "por", "com", "ao", "aos", "à", "às"}

# Siglas que continuam em maiusculo
SIGLAS = {
    "SAAE", "SAMAE", "SAE", "DML", "PMDV", "UASG", "SRP", "CNPJ", "CPF", "ME", "EPP",
    "TR", "ETP", "DFD", "SINAPI", "SICRO", "PVC", "CBUQ", "EPI", "EPIS", "LED", "ABNT",
    "NBR", "INMETRO", "ISO", "PEAD", "PPR", "CPVC", "MDF", "PET", "PP", "PE", "PCP",
    "PNCP", "BLL", "BNC", "SA", "S/A", "LTDA", "EIRELI", "MEI", "SUS", "UBS", "UPA",
    "CRAS", "CREAS", "CAPS", "SEMED", "SEMSA", "SEMOB", "DETRAN", "DER", "DNIT",
    "INSS", "IBGE", "TCU", "CGU", "CEP", "GLP", "GNV", "TI", "CFTV", "USB", "HDMI",
    "RJ45", "CAT5", "CAT6", "UTP", "AC", "CA", "CC", "AB", "II", "III", "IV", "VI",
    "VII", "VIII", "IX", "XI", "XII", "OM", "OMS", "RM", "BI", "CIA", "COMAR", "CTMRJ",
    "EB", "MB", "FAB", "MD", "GHC", "HNSC", "IPM", "RS", "SC", "PR",
}
SIGLAS_UF = UFS_VALIDAS


def _maiusculo_demais(texto, limite=0.6):
    letras = [c for c in texto if c.isalpha()]
    if len(letras) < 4:
        return False
    return sum(1 for c in letras if c.isupper()) / len(letras) >= limite


def _ajustar_palavra(palavra, inicio, modo):
    """modo 'nome' -> Capitaliza; modo 'frase' -> minuscula (exceto 1a)."""
    nucleo = palavra.strip(".,;:()[]\"'“”")
    if not nucleo:
        return palavra
    sem_pontos = nucleo.replace(".", "")
    # Siglas conhecidas, UF, numeros/codigos (A-18, 25KG, R-2) ficam como estao
    if (sem_pontos.upper() in SIGLAS or any(ch.isdigit() for ch in nucleo)
            or nucleo in ("I", "V", "X")):
        return palavra
    baixa = palavra.lower()
    if modo == "nome":
        if not inicio and nucleo.lower() in CONECTIVOS:
            return baixa
        # capitaliza cada parte de palavras compostas (Santa-Maria, D'Oeste)
        return re.sub(r"(^|[-'/(])([a-zà-ÿ])", lambda m: m.group(1) + m.group(2).upper(), baixa)
    # frase
    if inicio:
        return re.sub(r"^([^a-zà-ÿ]*)([a-zà-ÿ])", lambda m: m.group(1) + m.group(2).upper(), baixa, count=1)
    return baixa


def _uf_final(texto):
    """Mantem a UF maiuscula no final: 'Itaberai/go' -> 'Itaberai/GO'."""
    return re.sub(r"([/\- ])([A-Za-z]{2})(\)?)\s*$",
                  lambda m: m.group(1) + (m.group(2).upper() if m.group(2).upper() in SIGLAS_UF else m.group(2)) + m.group(3),
                  texto)


def padronizar_nome(texto):
    """Nome proprio (orgao, municipio). Cada trecho separado por ' / ' ou
    ' — ' e tratado sozinho, e so muda se estiver quase todo maiusculo
    (ex: 'MINISTERIO DA DEFESA / Comando do Exercito')."""
    if not texto:
        return texto
    trechos = re.split(r"(\s+/\s+|\s+[—–]\s+)", texto)
    if len(trechos) > 1:
        return "".join(t if re.fullmatch(r"\s+/\s+|\s+[—–]\s+", t) else _padronizar_nome_trecho(t)
                       for t in trechos)
    return _padronizar_nome_trecho(texto)


def _padronizar_nome_trecho(texto):
    if not texto or not _maiusculo_demais(texto):
        return texto
    partes = re.split(r"(\s+|/)", texto.strip())
    saida, inicio = [], True
    for p in partes:
        if not p or p.isspace() or p == "/":
            saida.append(p)
            if p == "/":
                inicio = True
            continue
        if p.upper() in SIGLAS_UF and saida and saida[-1] == "/":
            saida.append(p.upper())
        else:
            saida.append(_ajustar_palavra(p, inicio, "nome"))
        inicio = False
    resultado = "".join(saida)
    # UF entre parenteses: "(es)" -> "(ES)"; "S a" / "S/a" no final -> "S.A."
    resultado = re.sub(r"\(([A-Za-z]{2})\)",
                       lambda m: "(" + (m.group(1).upper() if m.group(1).upper() in SIGLAS_UF else m.group(1)) + ")",
                       resultado)
    resultado = re.sub(r"\bS[ /]?a\.?$", "S.A.", resultado)
    return _uf_final(resultado)


def padronizar_frase(texto):
    """Texto corrido (objeto, descricao de item). Primeira letra maiuscula,
    resto minusculo (siglas mantidas). O texto e quebrado em trechos (por
    virgula, ponto e virgula, parenteses) e so os trechos quase todo em
    maiusculo mudam: 'REGISTRO DE PRECOS, com validade' -> 'Registro de precos, com validade'."""
    if not texto:
        return texto
    trechos = re.split(r"([,;()\[\]]\s*)", texto.strip())
    saida = []
    primeiro = True
    for t in trechos:
        if not t:
            continue
        if re.fullmatch(r"[,;()\[\]]\s*", t):
            saida.append(t)
            continue
        saida.append(_padronizar_frase_trecho(t, primeiro) if _maiusculo_demais(t, 0.6)
                     else _corrigir_sequencias_maiusculas(t, primeiro))
        primeiro = False
    resultado = _uf_final("".join(saida).rstrip())
    # "ipiranga do norte – mt." / "tumiritinga-mg" -> UF maiuscula
    return re.sub(r"([–\-/]\s?)([a-z]{2})\b(?=[.,;)]|$)",
                  lambda m: m.group(1) + (m.group(2).upper() if m.group(2).upper() in SIGLAS_UF and m.group(2) != "se" else m.group(2)),
                  resultado)


def _palavra_toda_maiuscula(p):
    nucleo = p.strip(".,;:()[]\"'“”")
    letras = [c for c in nucleo if c.isalpha()]
    return (len(letras) >= 2 and all(c.isupper() for c in letras)
            and not any(c.isdigit() for c in nucleo)
            and nucleo.replace(".", "").upper() not in SIGLAS)


def _corrigir_sequencias_maiusculas(texto, inicio_texto):
    """Num trecho misto ('REGISTRO DE PRECOS para futura...'), passa para
    minusculo as sequencias de 2+ palavras TODAS EM MAIUSCULO."""
    partes = re.split(r"(\s+)", texto)
    palavras = [i for i, p in enumerate(partes) if p and not p.isspace()]
    marcar = set()
    seq = []
    for i in palavras + [None]:
        if i is not None and _palavra_toda_maiuscula(partes[i]):
            seq.append(i)
            continue
        if len(seq) >= 2:
            marcar.update(seq)
        seq = []
    for i in marcar:
        primeira = inicio_texto and i == palavras[0]
        partes[i] = _ajustar_palavra(partes[i], primeira, "frase")
    return "".join(partes)


def _padronizar_frase_trecho(texto, inicio_texto=True):
    partes = re.split(r"(\s+)", texto)
    saida, inicio = [], inicio_texto
    for p in partes:
        if not p or p.isspace():
            saida.append(p)
            continue
        saida.append(_ajustar_palavra(p, inicio, "frase"))
        inicio = p.endswith((".", "!", "?")) and len(p) > 3
    return "".join(saida)
