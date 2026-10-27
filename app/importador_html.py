"""
Importacao em lote a partir do HTML exportado pela Effecti.

O arquivo e uma sequencia de paginas HTML coladas, uma por licitacao, cada uma com:
  - "Dados da Licitacao": ID Effecti, Pregao SRP, Pregao, Uasg, UF, Modalidade,
    Data de Publicacao, Data Inicial, Data Final (= disputa), Link, Portal,
    Orgao, Objeto, Perfil
  - "Itens da Licitacao" (opcional): Grupo, Codigo, Tipo, Objeto, Qtde, Unid
    (so os itens que bateram na busca da Effecti, nao a lista completa)
  - "Anexos da Licitacao": link de download + nome de cada arquivo

O importador le o HTML, baixa os anexos para a pasta da conferencia e passa
cada processo pela mesma analise do pacote ZIP (edital, portal, valor,
municipio, CAPAG...). Nada e gravado no banco aqui.
"""
import os
import re
import time
import mimetypes
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from urllib.parse import unquote, urlparse

from app.capag import UFS_VALIDAS, normalizar
from app.importador_pacote import (analisar_processo, mapear_portal, detectar_portal_no_texto,
                                   marcar_duplicadas, _limpar, _para_float)
from app.texto import padronizar_frase
from app.itens_externos import buscar_itens

# Downloads
MAX_POR_ARQUIVO = 150 * 1024 * 1024   # 150 MB
TEMPO_TOTAL = 230                     # segundos para todos os downloads (gunicorn corta em 300)
BAIXAR_SIMULTANEOS = 8
CABECALHOS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/129.0 Safari/537.36"),
    "Accept": "*/*",
}


# ─── texto ───────────────────────────────────────────────────────────────────

def consertar_acentos(txt):
    """Conserta acento quebrado que as vezes vem da Effecti:
    'NÂº' -> 'Nº', 'ADMINISTRAÃÃO' -> 'ADMINISTRAÇÃO'."""
    txt = txt or ""
    if "Ã" not in txt and "Â" not in txt:
        return txt

    def _par(m):
        try:
            return m.group(0).encode("cp1252").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return m.group(0)

    txt = re.sub(r"[ÂÃ][\u0080-¿€‚ƒ„…†‡ˆ‰Š‹ŒŽ‘’“”•–—˜™š›œžŸ]", _par, txt)
    # O caractere do meio se perdeu: "ÃÃO" quase sempre era "ÇÃO"
    txt = re.sub(r"ÃÃ(?=[OoEe])", lambda m: "ÇÃ", txt)
    txt = re.sub(r"(?<=[a-z])Ã(?=[ao])", "ç", txt)
    return txt


def _texto(no):
    return _limpar(consertar_acentos(no.get_text() if no is not None else ""))


def _valor_html(txt):
    t = _limpar(txt)
    return None if t.lower() in ("", "undefined", "null", "none", "-") else t


def _data(txt):
    t = _valor_html(txt)
    if not t:
        return None
    for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y"):
        try:
            return datetime.strptime(t, fmt)
        except ValueError:
            continue
    return None


# ─── leitura do HTML ─────────────────────────────────────────────────────────

def _decodificar(conteudo):
    for cod in ("utf-8-sig", "cp1252"):
        try:
            return conteudo.decode(cod)
        except UnicodeDecodeError:
            continue
    return conteudo.decode("utf-8", errors="replace")


def _rotulo(txt):
    return normalizar(txt).rstrip(":").strip()


def _tabela_rotulos(tabela):
    """Linha so com <strong> = rotulos; a linha seguinte = valores, na mesma ordem."""
    dados, link = {}, None
    linhas = tabela.find_all("tr")
    for i, tr in enumerate(linhas):
        tds = tr.find_all("td", recursive=False)
        if not tds or not all(td.find("strong") for td in tds):
            continue
        if i + 1 >= len(linhas):
            break
        valores = linhas[i + 1].find_all("td", recursive=False)
        if valores and all(td.find("strong") for td in valores):
            continue
        for td_rot, td_val in zip(tds, valores):
            rot = _rotulo(td_rot.get_text())
            if rot == "link":
                a = td_val.find("a")
                link = a.get("href") if a else None
            else:
                dados[rot] = _texto(td_val)
    return dados, link


def _tabela_itens(tabela):
    itens, idx = [], None
    for tr in tabela.find_all("tr"):
        tds = tr.find_all("td", recursive=False)
        if not tds:
            continue
        if all(td.find("strong") for td in tds):
            idx = {_rotulo(td.get_text()): n for n, td in enumerate(tds)}
            continue
        if idx is None or len(tds) < 3:
            continue

        def col(nome):
            n = idx.get(nome)
            return _valor_html(_texto(tds[n])) if n is not None and n < len(tds) else None

        descricao = col("objeto") or col("descricao")
        if not descricao:
            continue
        grupo = col("grupo") or col("lote")
        if grupo and grupo.isdigit():
            grupo = f"Grupo {grupo}"
        qtd = _para_float(col("qtde") or col("quantidade"))
        itens.append({
            "numero_item": (col("codigo") or col("item") or "")[:20] or None,
            "lote_grupo": (grupo or "")[:100] or None,
            "descricao": padronizar_frase(descricao)[:500],
            "unidade": padronizar_frase(col("unid") or col("unidade") or "")[:50] or None,
            "quantidade": int(round(qtd)) if qtd is not None else None,
            "valor_estimado": None,
        })
    return itens


def _tabela_anexos(tabela):
    anexos = []
    for tr in tabela.find_all("tr"):
        a = tr.find("a")
        tds = tr.find_all("td", recursive=False)
        if not a or not a.get("href") or len(tds) < 2:
            continue
        anexos.append({"url": a["href"].strip(), "nome": _texto(tds[-1])})
    return anexos


def ler_html(conteudo):
    """Bytes do HTML -> lista de licitacoes (dicts), na ordem do arquivo."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(_decodificar(conteudo), "html.parser")
    blocos = []
    for h3 in soup.find_all("h3"):
        if _rotulo(h3.get_text()) != "dados da licitacao":
            continue
        tabela = h3.find_parent("table")
        if tabela is None:
            continue
        caixa = tabela.find_parent("div") or tabela.parent
        dados, link = _tabela_rotulos(tabela)
        itens, anexos = [], []
        for outra in caixa.find_all("table"):
            if outra is tabela:
                continue
            titulo = outra.find("h3")
            t = _rotulo(titulo.get_text()) if titulo else ""
            if t.startswith("itens"):
                itens = _tabela_itens(outra)
            elif t.startswith("anexos"):
                anexos = _tabela_anexos(outra)
        blocos.append(_montar(dados, link, itens, anexos))
    return blocos


def _montar(d, link, itens, anexos):
    def v(*nomes):
        for n in nomes:
            if _valor_html(d.get(n) or ""):
                return _valor_html(d[n])
        return None

    uf = (v("uf") or "").upper()
    orgao = re.sub(r"^PM\s+DE\b", "PREFEITURA MUNICIPAL DE", v("orgao") or "", flags=re.I)
    return {
        "id_effecti": v("id effecti") or "",
        "srp": normalizar(v("pregao srp") or "") == "sim",
        "numero": v("pregao", "numero", "edital") or "",
        "uasg": v("uasg") or "",
        "uf": uf if uf in UFS_VALIDAS else None,
        "modalidade": v("modalidade") or "",
        "data_publicacao": _data(v("data de publicacao")),
        "data_inicial": _data(v("data inicial")),
        "data_final": _data(v("data final")),
        "link": link or "",
        "portal": v("portal") or "",
        "orgao": orgao,
        "objeto": v("objeto") or "",
        "perfil": v("perfil") or "",
        "itens": itens,
        "anexos": anexos,
    }


# ─── download dos anexos ─────────────────────────────────────────────────────

def _nome_seguro(nome, url, resposta=None):
    nome = re.sub(r"\s*\(\d+(?:[.,]\d+)?\s*[KMG]B\)\s*$", "", nome or "", flags=re.I)  # "(6,00 MB)"
    nome = nome.split("\\")[-1].replace("/", "-").strip()  # "pasta\\arquivo.pdf"; "556/2026" vira "556-2026"
    if resposta is not None:
        cd = resposta.headers.get("Content-Disposition", "")
        m = re.search(r"filename\*=(?:UTF-8'')?([^;]+)", cd, re.I) or re.search(r'filename="?([^";]+)"?', cd, re.I)
        nome_servidor = unquote(m.group(1)).strip().strip('"') if m else ""
        if nome_servidor and (not nome or not os.path.splitext(nome)[1]):
            nome = consertar_acentos(nome_servidor)
    if not nome:
        nome = unquote(os.path.basename(urlparse(url).path)) or "arquivo"
    valida = re.compile(r"\.[a-z0-9]{2,5}")
    ext = os.path.splitext(nome)[1].lower()
    if not valida.fullmatch(ext or ""):
        # extensao pelo link (".../edital.pdf/1") ou pelo tipo do arquivo
        ext = None
        for parte in reversed([s for s in urlparse(url).path.split("/") if s]):
            e = os.path.splitext(unquote(parte))[1].lower()
            if valida.fullmatch(e or ""):
                ext = e
                break
        if not ext:
            tipo = (resposta.headers.get("Content-Type", "") if resposta is not None else "").split(";")[0].strip()
            ext = mimetypes.guess_extension(tipo) if tipo else None
            if ext in (None, ".bin", ".ksh", ".txt"):
                ext = ".pdf"
        nome = re.sub(r"[.:\s]+$", "", nome) + ext
    nome = re.sub(r'[<>:"|?*\x00-\x1f]', "_", nome).strip(". ") or f"arquivo{ext}"
    return nome[:180]


def _baixar(anexo, pasta, prazo):
    """Baixa um anexo. Devolve dict com nome/caminho/tamanho ou erro."""
    import requests

    url = anexo["url"]
    if not url.lower().startswith(("http://", "https://")):
        return {**anexo, "erro": "link inválido"}
    if time.time() > prazo:
        return {**anexo, "erro": "tempo esgotado"}
    try:
        with requests.get(url, headers=CABECALHOS, stream=True, timeout=(15, 60),
                          allow_redirects=True) as r:
            if r.status_code >= 400:
                return {**anexo, "erro": f"o site respondeu {r.status_code}"}
            tipo = r.headers.get("Content-Type", "").lower()
            nome = _nome_seguro(anexo["nome"], url, r)
            if "text/html" in tipo and not nome.lower().endswith((".htm", ".html")):
                return {**anexo, "erro": "o link abre uma página, não o arquivo"}
            os.makedirs(pasta, exist_ok=True)
            base, ext = os.path.splitext(nome)
            caminho, n = os.path.join(pasta, nome), 2
            while os.path.exists(caminho):
                caminho = os.path.join(pasta, f"{base} ({n}){ext}")
                n += 1
            total = 0
            with open(caminho, "wb") as f:
                for pedaco in r.iter_content(256 * 1024):
                    total += len(pedaco)
                    if total > MAX_POR_ARQUIVO or time.time() > prazo + 30:
                        f.close()
                        os.remove(caminho)
                        return {**anexo, "erro": "arquivo grande demais" if total > MAX_POR_ARQUIVO
                                else "tempo esgotado"}
                    f.write(pedaco)
            if total == 0:
                os.remove(caminho)
                return {**anexo, "erro": "arquivo vazio"}
            return {**anexo, "nome": os.path.basename(caminho), "caminho": caminho, "tamanho": total}
    except Exception as e:
        return {**anexo, "erro": type(e).__name__}


def baixar_anexos(licitacoes, destino):
    """Baixa os anexos de todas as licitacoes em paralelo.
    Cada licitacao ganha 'baixados' [(nome, caminho, tamanho)] e 'falhas' [anexo]."""
    prazo = time.time() + TEMPO_TOTAL
    tarefas = []
    with ThreadPoolExecutor(max_workers=BAIXAR_SIMULTANEOS) as pool:
        for i, lic in enumerate(licitacoes):
            pasta = os.path.join(destino, f"{i:03d}")
            for anexo in lic["anexos"]:
                tarefas.append((i, pool.submit(_baixar, anexo, pasta, prazo)))
        for lic in licitacoes:
            lic["baixados"], lic["falhas"] = [], []
        for i, futuro in tarefas:
            r = futuro.result()
            if r.get("erro"):
                licitacoes[i]["falhas"].append(r)
            else:
                licitacoes[i]["baixados"].append((r["nome"], r["caminho"], r["tamanho"]))


# ─── analise ─────────────────────────────────────────────────────────────────

RE_PORTAL_OBJETO = re.compile(r"^\s*\[([^\]]+)\]\s*-?\s*")


def _portal_da_licitacao(lic):
    """Portal da disputa pelos dados da Effecti (None = descobrir pelo edital).
    PNCP e TCE sao so onde o edital foi publicado, nao onde a disputa acontece."""
    m = RE_PORTAL_OBJETO.match(lic["objeto"])
    if m:
        p = mapear_portal(m.group(1)) or detectar_portal_no_texto(m.group(1))
        if p:
            return p
    p = mapear_portal(lic["portal"])
    if p:
        return p
    if "pncp.gov.br" not in lic["link"]:
        return detectar_portal_no_texto(lic["link"])
    return None


def _numero_bruto(numero):
    """'PCE 63/2026' -> '632026' (o formatar_numero separa o ano depois)."""
    m = re.search(r"(\d+)\s*/\s*(\d{4})", numero or "")
    if m:
        return m.group(1) + m.group(2)
    return numero


def analisar_html(conteudo, destino, municipios=None, agora=None):
    """Le o HTML, baixa os anexos em destino/ e devolve o mesmo formato do
    analisar_pacote: {"propostas": [...], "avisos": [...], "tem_resumo": True}."""
    agora = agora or datetime.now()
    licitacoes = ler_html(conteudo)
    if not licitacoes:
        return {"propostas": [], "avisos": [], "tem_resumo": True}

    baixar_anexos(licitacoes, destino)

    propostas = []
    for lic in licitacoes:
        objeto = RE_PORTAL_OBJETO.sub("", lic["objeto"])
        data = lic["data_final"]
        hora_vazia = data is not None and data.hour == 0 and data.minute == 0
        info = {
            "codigo_busca": f"EF-{lic['id_effecti']}" if lic["id_effecti"] else "",
            "objeto": objeto,
            "uasg": lic["uasg"],
            "comprador": lic["orgao"],
            "data_publicacao": lic["data_publicacao"],
            "data_disputa": data,
            "hora_disputa_vazia": hora_vazia,
            "portal": lic["portal"],
            "portal_definido": _portal_da_licitacao(lic),
            "uf": lic["uf"],
            "valor_estimado": None,
            "itens": lic["itens"],
        }
        rotulo = f"Effecti {lic['id_effecti']}" if lic["id_effecti"] else (lic["orgao"] or "processo")
        p = analisar_processo(rotulo, lic["baixados"], info, _numero_bruto(lic["numero"]),
                              None, lic["orgao"], municipios, agora)

        p["link_edital"] = lic["link"][:500]
        p["perfil"] = lic["perfil"]
        p["modalidade"] = lic["modalidade"]
        p["srp"] = lic["srp"]
        p["id_effecti"] = lic["id_effecti"]
        p["links_falhos"] = [{"nome": f["nome"], "url": f["url"], "erro": f["erro"]} for f in lic["falhas"]]
        if lic["falhas"]:
            p["avisos"].append(f"{len(lic['falhas'])} anexo(s) não baixaram — veja os links abaixo e anexe à mão.")
        if not lic["anexos"]:
            p["avisos"].append("A Effecti não trouxe nenhum anexo.")
        propostas.append(p)

    # A Effecti so traz os itens que bateram na busca: a lista completa vem do
    # PNCP ou da planilha anexada (em paralelo, porque cada busca leva alguns segundos)
    def _itens(par):
        lic, p = par
        try:
            return buscar_itens(link=lic["link"], orgao=lic["orgao"], numero=lic["numero"],
                                uf=lic["uf"], data=lic["data_final"], objeto=lic["objeto"],
                                municipio=p.get("municipio"),
                                arquivos=[(n, c) for n, c, _t in lic["baixados"]])
        except Exception:
            return [], None

    with ThreadPoolExecutor(max_workers=6) as pool:
        resultados = list(pool.map(_itens, zip(licitacoes, propostas)))
    for (lic, p), (itens, fonte) in zip(zip(licitacoes, propostas), resultados):
        p["fonte_itens"] = fonte
        if itens:
            p["itens"] = itens
            p["avisos"].append(f"{len(itens)} item(ns) lidos do {fonte}.")
        elif lic["itens"]:
            p["avisos"].append(f"Não achei a lista completa de itens (PNCP/planilha). A Effecti trouxe só "
                               f"{len(lic['itens'])} item(ns) — complete pelo TR.")
        else:
            p["avisos"].append("Não achei os itens no PNCP nem em planilha — preencha pelo TR.")

    marcar_duplicadas(propostas, preferir=lambda p: (p.get("portal") or "") != "PNCP"
                      and "pncp.gov.br" not in (p.get("link_edital") or ""))
    return {"propostas": propostas, "avisos": [], "tem_resumo": True}
