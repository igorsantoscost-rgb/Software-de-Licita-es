"""
Prospeccao semanal (toda segunda-feira) de licitacoes HOMOLOGADAS no PNCP, pelas palavras-chave
cadastradas de cada cliente (so clientes com "prospectar homologadas" ligado).

Para cada item homologado que combina com o cliente, guarda:
  - o vencedor (CNPJ, razao social, contato) -> venda indireta: o cliente
    oferece o produto para quem ganhou e vai precisar entregar;
  - se o pregao e registro de precos, a validade da ata -> carona/adesao.

Como funciona:
  1. Busca no PNCP os editais encerrados (e atas vigentes) com cada palavra-chave.
  2. Cada pregao achado vira uma CompraMonitorada.
  3. Pregoes ainda sem resultado sao verificados de novo nos dias seguintes
     (ate DIAS_ACOMPANHAR), ate os itens de interesse serem homologados.
  4. Itens homologados viram OportunidadeHomologada (uma por item/vencedor).
  5. As novas sao avisadas por e-mail aos assessores do cliente.

Uso pelo terminal: python -m app.prospeccao
"""
import logging
import os
import re
import time
from datetime import datetime, timedelta, date

import requests

from app import db
from app.models import Cliente, CompraMonitorada, OportunidadeHomologada

logger = logging.getLogger(__name__)

API = "https://pncp.gov.br/api/pncp/v1"
BUSCA = "https://pncp.gov.br/api/search/"
CABECALHOS = {"User-Agent": "Mozilla/5.0 (compatible; Bidfy/1.0; +https://licitabidfy.com.br)",
              "Accept": "application/json"}
TIMEOUT = (10, 40)
PAUSA = 0.8               # segundos entre chamadas (o PNCP corta quem consulta rapido demais)
PAGINAS_PRIMEIRA = 8      # primeira busca de uma palavra: ate 8 paginas (~400 editais)
PAGINAS_DIA = 3           # buscas seguintes: so o comeco da lista (o que e novo)
TAM_PAGINA = 50
DIAS_ACOMPANHAR = 120     # pregao sem resultado e verificado por ate 4 meses
LIMITE_COMPRAS = 600      # pregoes verificados por rodada
LIMITE_MINUTOS = 40       # tempo maximo de uma rodada
LIMITE_CONTATOS = 80      # consultas de CNPJ (contato do vencedor) por rodada

# So entram itens homologados a partir desta data (padrao: 01/10/2026).
# Pode ser mudada pela variavel de ambiente PROSPECCAO_DESDE=AAAA-MM-DD.
try:
    DESDE = datetime.strptime(os.environ.get("PROSPECCAO_DESDE", "2026-10-01"), "%Y-%m-%d")
except ValueError:
    DESDE = datetime(2026, 10, 1)

RE_IDS_URL = re.compile(r"(?:editais|compras|atas)/(\d{14})/(\d{4})/(\d+)")


# ─── acesso ao PNCP ──────────────────────────────────────────────────────────

def _get(url, params=None, tentativas=3):
    """GET com novas tentativas. Devolve o JSON, [] para 204/404, ou None se falhar."""
    for n in range(tentativas):
        try:
            r = requests.get(url, params=params, headers=CABECALHOS, timeout=TIMEOUT)
            time.sleep(PAUSA)
            if r.status_code == 200:
                return r.json() if r.content else []
            if r.status_code in (204, 404):
                return []
            if r.status_code == 400:
                return None
        except Exception as e:
            logger.warning("PNCP %s: %s", url, e)
        time.sleep(2 * (n + 1))
    return None


def _ids(item):
    """(cnpj, ano, sequencial) do PREGAO de um resultado da busca do PNCP.
    Numa ata, numero_sequencial e o numero da ata; o do pregao esta no link
    (/atas/<cnpj>/<ano>/<sequencial do pregao>/<numero da ata>)."""
    url = item.get("item_url") or ""
    if "atas/" in url:
        m = RE_IDS_URL.search(url)
        return (m.group(1), int(m.group(2)), int(m.group(3))) if m else None
    if item.get("orgao_cnpj") and item.get("ano") and item.get("numero_sequencial"):
        try:
            return str(item["orgao_cnpj"]), int(item["ano"]), int(item["numero_sequencial"])
        except (TypeError, ValueError):
            pass
    m = RE_IDS_URL.search(item.get("item_url") or "")
    return (m.group(1), int(m.group(2)), int(m.group(3))) if m else None


def _data(txt):
    if not txt:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(txt)[:26].rstrip("Z"), fmt)
        except ValueError:
            continue
    return None


def buscar_editais(palavra, tipo="edital", status="encerradas", paginas=3, conhecidos=None):
    """Editais (ou atas) do PNCP com a palavra. Para cedo quando duas paginas
    seguidas so trazem pregoes ja conhecidos. Devolve [(ids, item)]."""
    achados, repetidas = [], 0
    for pagina in range(1, paginas + 1):
        params = {"q": palavra, "tipos_documento": tipo, "ordenacao": "-data",
                  "pagina": pagina, "tam_pagina": TAM_PAGINA}
        if status:
            params["status"] = status
        dados = _get(BUSCA, params)
        if dados is None and status:  # status nao aceito: busca sem ele
            params.pop("status")
            dados = _get(BUSCA, params)
        itens = (dados or {}).get("items") if isinstance(dados, dict) else None
        if not itens:
            break
        novos = 0
        for it in itens:
            ids = _ids(it)
            if not ids:
                continue
            achados.append((ids, it))
            if conhecidos is None or ids not in conhecidos:
                novos += 1
        repetidas = repetidas + 1 if novos == 0 else 0
        if repetidas >= 2 or len(itens) < TAM_PAGINA:
            break
    return achados


CONSULTA = "https://pncp.gov.br/api/consulta/v1"


def cabecalho_compra(cnpj, ano, seq):
    """Dados do pregao (objeto, numero, modalidade, SRP, orgao). Tenta a API do
    PNCP e, se nao vier o objeto, a API de consulta."""
    # A API de consulta e a que responde hoje; a outra devolve 301 (so reserva).
    for base, tentativas in ((CONSULTA, 3), (API, 1)):
        d = _get(f"{base}/orgaos/{cnpj}/compras/{ano}/{seq}", tentativas=tentativas)
        if isinstance(d, dict) and (d.get("objetoCompra") or d.get("numeroCompra")):
            return d
    return {}


def _dados_cabecalho(cab):
    """Campos da oportunidade que vem do cabecalho do pregao."""
    if not cab:
        return {}
    orgao = cab.get("orgaoEntidade") or {}
    unidade = cab.get("unidadeOrgao") or {}
    num = cab.get("numeroCompra")
    return {
        "numero_compra": f"{num}/{cab.get('anoCompra')}" if num and cab.get("anoCompra") else (str(num) if num else None),
        "objeto": cab.get("objetoCompra") or None,
        "modalidade": (cab.get("modalidadeNome") or "")[:80] or None,
        "srp": bool(cab.get("srp")) if cab.get("srp") is not None else None,
        "orgao": (orgao.get("razaoSocial") or "")[:300] or None,
        "uf": (unidade.get("ufSigla") or "")[:2] or None,
        "municipio": (unidade.get("municipioNome") or "")[:150] or None,
        "valor_total": _num(cab.get("valorTotalHomologado")) or None,
    }


def completar_compras(limite=120, log=print):
    """Preenche objeto, numero e valor total do pregao das oportunidades que
    ficaram sem. Devolve (pregoes consultados, pregoes completados)."""
    pend = (db.session.query(OportunidadeHomologada.cnpj_orgao, OportunidadeHomologada.ano,
                             OportunidadeHomologada.sequencial)
            .filter((OportunidadeHomologada.objeto.is_(None)) | (OportunidadeHomologada.numero_compra.is_(None))
                    | (OportunidadeHomologada.valor_total_licitacao.is_(None)))
            .distinct().limit(limite).all())
    ok = 0
    for cnpj, ano, seq in pend:
        d = _dados_cabecalho(cabecalho_compra(cnpj, ano, seq))
        if not d.get("objeto") and not d.get("numero_compra") and not d.get("valor_total"):
            continue
        for op in OportunidadeHomologada.query.filter_by(cnpj_orgao=cnpj, ano=ano, sequencial=seq).all():
            op.objeto = op.objeto or d["objeto"]
            op.numero_compra = op.numero_compra or d["numero_compra"]
            op.modalidade = op.modalidade or d["modalidade"]
            op.valor_total_licitacao = d["valor_total"] or op.valor_total_licitacao
            if op.srp is None:
                op.srp = d["srp"]
            if d["orgao"]:
                op.orgao = d["orgao"]
            op.uf = op.uf or d["uf"]
            op.municipio = op.municipio or d["municipio"]
        db.session.commit()
        ok += 1
    log(f"Pregões sem objeto/número/valor total: {len(pend)} consultado(s), {ok} completado(s).")
    return len(pend), ok


def itens_compra(cnpj, ano, seq):
    todos, pagina = [], 1
    while pagina <= 20:
        dados = _get(f"{API}/orgaos/{cnpj}/compras/{ano}/{seq}/itens",
                     {"pagina": pagina, "tamanhoPagina": 500})
        if dados is None:
            return None
        lote = dados.get("itens") if isinstance(dados, dict) else dados
        if not lote:
            break
        todos.extend(lote)
        if len(lote) < 500:
            break
        pagina += 1
    return todos


def resultados_item(cnpj, ano, seq, numero_item):
    dados = _get(f"{API}/orgaos/{cnpj}/compras/{ano}/{seq}/itens/{numero_item}/resultados")
    if dados is None:
        return None
    if isinstance(dados, dict):
        dados = dados.get("listaResultados") or dados.get("resultados") or []
    return dados


def _local_orgao(cnpj, cache):
    """UF e cidade do orgao pelo CNPJ (quando o PNCP nao informa)."""
    chave = "orgao:" + cnpj
    if chave not in cache:
        cache[chave] = contato_fornecedor(cnpj)
    return cache[chave] or {}


def completar_ufs(limite=400):
    """Preenche UF/cidade das oportunidades que ficaram sem (versoes antigas)."""
    cache, feitas = {}, 0
    sem = (OportunidadeHomologada.query.filter(OportunidadeHomologada.uf.is_(None))
           .limit(limite).all())
    for op in sem:
        c = CompraMonitorada.query.filter_by(cnpj=op.cnpj_orgao, ano=op.ano, sequencial=op.sequencial).first()
        uf, mun = (c.uf, c.municipio) if c else (None, None)
        if not uf:
            lugar = _local_orgao(op.cnpj_orgao, cache)
            uf, mun = lugar.get("uf") or None, mun or lugar.get("municipio") or None
        if uf:
            op.uf, op.municipio = uf, op.municipio or mun
            feitas += 1
        if not op.fornecedor_uf and op.fornecedor_cnpj and len(op.fornecedor_cnpj) == 14:
            ct = cache.get(op.fornecedor_cnpj)
            if ct is None:
                ct = cache[op.fornecedor_cnpj] = contato_fornecedor(op.fornecedor_cnpj)
            if ct:
                op.fornecedor_uf = op.fornecedor_uf or ct.get("uf") or None
                op.fornecedor_municipio = op.fornecedor_municipio or ct.get("municipio") or None
                op.fornecedor_telefone = op.fornecedor_telefone or ct.get("telefone") or None
                op.fornecedor_email = op.fornecedor_email or ct.get("email") or None
    db.session.commit()
    return feitas


def contato_fornecedor(cnpj):
    """Razao social, UF, cidade, telefone e e-mail do vencedor (BrasilAPI)."""
    d = re.sub(r"\D", "", cnpj or "")
    if len(d) != 14:
        return {}
    try:
        r = requests.get(f"https://brasilapi.com.br/api/cnpj/v1/{d}", timeout=10,
                         headers={"User-Agent": "Bidfy/1.0"})
        if r.status_code != 200:
            return {}
        j = r.json()
    except Exception:
        return {}
    fones = [f for f in (j.get("ddd_telefone_1"), j.get("ddd_telefone_2")) if f]
    return {"nome": j.get("razao_social") or j.get("nome_fantasia"),
            "uf": (j.get("uf") or "")[:2], "municipio": (j.get("municipio") or "").title(),
            "telefone": " / ".join(fones)[:80], "email": (j.get("email") or "").lower()[:200]}


# ─── palavras-chave ──────────────────────────────────────────────────────────

def _padroes_clientes():
    """[(cliente, [(palavra, regex)])] dos clientes com prospeccao ligada."""
    from app.classificador_cliente import _padrao
    saida = []
    for c in Cliente.query.filter(Cliente.prospectar_homologadas.is_(True)).all():
        pares = [(p.palavra, _padrao(p.palavra)) for p in c.palavras_chave]
        pares = [(p, rx) for p, rx in pares if rx]
        if pares:
            saida.append((c, pares))
    return saida


CABECA = 80  # quantos caracteres do inicio da descricao contam como "nome do produto"


def _cabeca(texto):
    """Inicio da descricao, onde fica o nome do produto. A palavra-chave tem que
    estar aqui: no meio da especificacao ela costuma ser so um detalhe
    (ex.: tambor de lixo "com pintura em esmalte sintetico")."""
    from app.classificador_cliente import _normalizar
    t = _normalizar(texto)
    if len(t) <= CABECA:
        return t
    corte = t.rfind(" ", 0, CABECA + 1)
    return t[:corte if corte > 20 else CABECA]


def _combina(pares, texto, complemento=""):
    """Palavras-chave que aparecem no nome do item. Se a descricao e generica
    (nao tem nenhuma), olha o inicio da informacao complementar."""
    achou = [p for p, rx in pares if rx.search(_cabeca(texto))]
    if not achou and complemento:
        achou = [p for p, rx in pares if rx.search(_cabeca(complemento))]
    return achou


# ─── processamento de um pregao ──────────────────────────────────────────────

def _num(v):
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def processar_compra(compra, clientes, contatos_cache, contador_contatos):
    """Verifica os itens do pregao e grava as oportunidades homologadas.
    Devolve quantas oportunidades novas foram criadas."""
    cnpj, ano, seq = compra.cnpj, compra.ano, compra.sequencial
    itens = itens_compra(cnpj, ano, seq)
    compra.ultima_verificacao = datetime.utcnow()
    if itens is None:
        compra.erro = "PNCP não respondeu"
        return 0
    compra.erro = None
    cab = None
    novas, pendentes, interesse = 0, 0, 0
    for it in itens:
        texto = f"{it.get('descricao') or ''} {it.get('informacaoComplementar') or ''}"
        alvo = [(c, _combina(pares, it.get('descricao') or '', it.get('informacaoComplementar') or ''))
                for c, pares in clientes]
        alvo = [(c, ps) for c, ps in alvo if ps]
        if not alvo:
            continue
        interesse += 1
        numero = it.get("numeroItem")
        if not it.get("temResultado"):
            pendentes += 1
            continue
        resultados = resultados_item(cnpj, ano, seq, numero)
        if resultados is None:
            pendentes += 1
            continue
        if cab is None:
            cab = cabecalho_compra(cnpj, ano, seq)
        for res in resultados:
            if res.get("dataCancelamento") or "cancel" in str(res.get("situacaoCompraItemResultadoNome") or "").lower():
                continue
            data_res = _data(res.get("dataResultado"))
            if data_res and data_res < DESDE:
                continue  # homologado antes do periodo de interesse
            forn = re.sub(r"\D", "", str(res.get("niFornecedor") or "")) or str(res.get("niFornecedor") or "")
            for cliente, palavras in alvo:
                existe = OportunidadeHomologada.query.filter_by(
                    cliente_id=cliente.id, cnpj_orgao=cnpj, ano=ano, sequencial=seq,
                    numero_item=int(numero), fornecedor_cnpj=forn).first()
                if existe:
                    continue
                if forn not in contatos_cache and contador_contatos[0] < LIMITE_CONTATOS:
                    contatos_cache[forn] = contato_fornecedor(forn)
                    contador_contatos[0] += 1
                ct = contatos_cache.get(forn) or {}
                dc = _dados_cabecalho(cab)
                uf_orgao = dc.get("uf") or (compra.uf or "")[:2] or None
                mun_orgao = dc.get("municipio") or (compra.municipio or "")[:150] or None
                if not uf_orgao:
                    lugar = _local_orgao(cnpj, contatos_cache)
                    uf_orgao, mun_orgao = lugar.get("uf") or None, mun_orgao or lugar.get("municipio") or None
                op = OportunidadeHomologada(
                    cliente_id=cliente.id, cnpj_orgao=cnpj, ano=ano, sequencial=seq,
                    numero_item=int(numero),
                    orgao=(dc.get("orgao") or compra.titulo or "")[:300],
                    uf=uf_orgao,
                    municipio=mun_orgao,
                    numero_compra=dc.get("numero_compra"),
                    modalidade=dc.get("modalidade"),
                    objeto=dc.get("objeto"),
                    srp=dc.get("srp"),
                    valor_total_licitacao=dc.get("valor_total"),
                    ata_vigencia_fim=compra.ata_vigencia_fim,
                    descricao_item=" ".join(texto.split())[:2000],
                    unidade=(it.get("unidadeMedida") or "")[:60],
                    quantidade=_num(it.get("quantidade")),
                    valor_unitario_estimado=_num(it.get("valorUnitarioEstimado")),
                    fornecedor_cnpj=forn[:20],
                    fornecedor_nome=(res.get("nomeRazaoSocialFornecedor") or ct.get("nome") or "")[:300],
                    fornecedor_uf=ct.get("uf") or None,
                    fornecedor_municipio=ct.get("municipio") or None,
                    fornecedor_telefone=ct.get("telefone") or None,
                    fornecedor_email=ct.get("email") or None,
                    quantidade_homologada=_num(res.get("quantidadeHomologada")),
                    valor_unitario_homologado=_num(res.get("valorUnitarioHomologado")),
                    data_resultado=data_res,
                    palavras=", ".join(palavras)[:300],
                )
                db.session.add(op)
                novas += 1
    idade = datetime.utcnow() - (compra.primeira_vez or datetime.utcnow())
    compra.concluida = (interesse == 0 or pendentes == 0 or idade > timedelta(days=DIAS_ACOMPANHAR))
    db.session.commit()
    return novas


# ─── rodada diaria ───────────────────────────────────────────────────────────

def rodar_prospeccao(enviar_email=True, log=print):
    """Rodada completa: busca, verifica os pregoes e avisa por e-mail.
    Devolve dict com os totais."""
    inicio = time.time()
    clientes = _padroes_clientes()
    if not clientes:
        log("Prospecção: nenhum cliente com prospecção ligada e palavras-chave.")
        return {"clientes": 0, "buscados": 0, "verificados": 0, "novas": 0}

    conhecidos = {(c.cnpj, c.ano, c.sequencial) for c in
                  CompraMonitorada.query.with_entities(CompraMonitorada.cnpj, CompraMonitorada.ano,
                                                       CompraMonitorada.sequencial)}
    primeira = not conhecidos
    palavras = sorted({p for _c, pares in clientes for p, _rx in pares}, key=str.lower)
    buscados = 0
    for palavra in palavras:
        paginas = PAGINAS_PRIMEIRA if primeira else PAGINAS_DIA
        achados = buscar_editais(palavra, "edital", "encerradas", paginas, conhecidos)
        achados += buscar_editais(palavra, "ata", "vigente", paginas, conhecidos)
        for ids, it in achados:
            fim_ata = _data(it.get("data_fim_vigencia")) if it.get("document_type") == "ata" or "atas/" in (it.get("item_url") or "") else None
            if ids in conhecidos:
                if fim_ata:
                    c = CompraMonitorada.query.filter_by(cnpj=ids[0], ano=ids[1], sequencial=ids[2]).first()
                    if c and (not c.ata_vigencia_fim or c.ata_vigencia_fim < fim_ata.date()):
                        c.ata_vigencia_fim = fim_ata.date()
                        OportunidadeHomologada.query.filter_by(
                            cnpj_orgao=ids[0], ano=ids[1], sequencial=ids[2]).update(
                            {"ata_vigencia_fim": fim_ata.date()}, synchronize_session=False)
                continue
            uf_busca = (it.get("uf") or "").upper()[:2] or None
            db.session.add(CompraMonitorada(cnpj=ids[0], ano=ids[1], sequencial=ids[2],
                                            titulo=(it.get("orgao_nome") or it.get("title") or "")[:300],
                                            uf=uf_busca, municipio=(it.get("municipio_nome") or "")[:150] or None,
                                            ata_vigencia_fim=fim_ata.date() if fim_ata else None))
            conhecidos.add(ids)
            buscados += 1
        db.session.commit()
        if time.time() - inicio > LIMITE_MINUTOS * 60 / 2:
            break
    log(f"Prospecção: {len(palavras)} palavra(s), {buscados} pregão(ões) novo(s) no acompanhamento.")

    pendentes = (CompraMonitorada.query.filter_by(concluida=False)
                 .order_by(CompraMonitorada.ultima_verificacao.asc().nullsfirst())
                 .limit(LIMITE_COMPRAS).all())
    contatos, contador = {}, [0]
    verificados, novas = 0, 0
    for compra in pendentes:
        if time.time() - inicio > LIMITE_MINUTOS * 60:
            break
        try:
            novas += processar_compra(compra, clientes, contatos, contador)
        except Exception as e:
            db.session.rollback()
            logger.exception("Prospecção: falha no pregão %s/%s/%s", compra.cnpj, compra.ano, compra.sequencial)
            compra.erro = str(e)[:300]
            compra.ultima_verificacao = datetime.utcnow()
            db.session.commit()
        verificados += 1
    log(f"Prospecção: {verificados} pregão(ões) verificado(s), {novas} oportunidade(s) nova(s).")
    try:
        completar_ufs()
        completar_compras(log=log)
    except Exception:
        db.session.rollback()

    avisados = enviar_relatorio_semanal() if enviar_email else 0
    if avisados:
        log(f"Prospecção: relatório de leads enviado para {avisados} cliente(s).")
    return {"clientes": len(clientes), "buscados": buscados, "verificados": verificados, "novas": novas}


# ─── e-mail diario ───────────────────────────────────────────────────────────

def limpar_fora_do_nome():
    """Remove as oportunidades (novas ou vistas) cuja palavra-chave so aparecia
    no meio da especificacao. Devolve quantas saíram."""
    from app.classificador_cliente import _padrao
    padroes, removidas = {}, 0
    for op in OportunidadeHomologada.query.filter(OportunidadeHomologada.situacao.in_(("nova", "vista"))).all():
        if op.cliente_id not in padroes:
            c = Cliente.query.get(op.cliente_id)
            pares = [(p.palavra, _padrao(p.palavra)) for p in (c.palavras_chave if c else [])]
            padroes[op.cliente_id] = [(p, rx) for p, rx in pares if rx]
        if padroes[op.cliente_id] and not _combina(padroes[op.cliente_id], op.descricao_item or ""):
            db.session.delete(op)
            removidas += 1
    db.session.commit()
    return removidas


def ultima_semana(hoje=None):
    """Segunda e domingo da ultima semana completa (seg-dom) antes de hoje."""
    from app.models import agora_brasilia
    hoje = hoje or agora_brasilia().date()
    seg = hoje - timedelta(days=hoje.weekday() + 7)
    return seg, seg + timedelta(days=6)


def semanas_disponiveis(hoje=None):
    """Semanas completas (segunda-feira de cada uma) desde DESDE, a mais recente primeiro."""
    seg, _ = ultima_semana(hoje)
    inicio = DESDE.date() - timedelta(days=DESDE.weekday())
    semanas = []
    while seg >= inicio and len(semanas) < 60:
        semanas.append(seg)
        seg -= timedelta(days=7)
    return semanas


def agrupar_por_licitacao(ops):
    """{(cnpj, ano, seq): [ops]} mantendo a ordem de chegada."""
    grupos = {}
    for op in ops:
        grupos.setdefault((op.cnpj_orgao, op.ano, op.sequencial), []).append(op)
    return grupos


def enviar_relatorio_semanal(hoje=None):
    """RELATORIO DE LEADS - LICITACOES: um e-mail por cliente com as licitacoes
    homologadas na ultima semana completa (objeto, vencedor e telefone)."""
    from app.email_service import _enviar, _template_base, _emails_do_cliente, BASE_URL
    seg, dom = ultima_semana(hoje)
    ini, fim = datetime.combine(seg, datetime.min.time()), datetime.combine(dom + timedelta(days=1), datetime.min.time())
    enviados = 0
    for cliente in Cliente.query.filter_by(prospectar_homologadas=True).all():
        ops = (OportunidadeHomologada.query
               .filter(OportunidadeHomologada.cliente_id == cliente.id,
                       OportunidadeHomologada.situacao != "descartada",
                       OportunidadeHomologada.data_resultado >= ini,
                       OportunidadeHomologada.data_resultado < fim)
               .order_by(OportunidadeHomologada.data_resultado.desc(), OportunidadeHomologada.numero_item).all())
        destinos = _emails_do_cliente(cliente)
        if not ops or not destinos:
            continue
        grupos = agrupar_por_licitacao(ops)
        td = "padding:8px;border-bottom:1px solid #eee;vertical-align:top"
        linhas = []
        for itens in list(grupos.values())[:60]:
            op = itens[0]
            objeto = (op.objeto or op.descricao_item or "")[:220]
            local = f"{op.orgao or ''}{' — ' + (op.municipio + '/' if op.municipio else '') + op.uf if op.uf else ''}"
            venc, vistos = [], set()
            for it in itens:
                chave = it.fornecedor_cnpj or it.fornecedor_nome
                if chave in vistos:
                    continue
                vistos.add(chave)
                venc.append((it.fornecedor_nome or it.fornecedor_cnpj or "—", it.fornecedor_telefone))
            nomes = "<br>".join(n[:60] for n, _t in venc[:4]) + (f"<br><small>+ {len(venc) - 4}</small>" if len(venc) > 4 else "")
            fones = "<br>".join((t or "—") for _n, t in venc[:4])
            linhas.append(
                f"<tr><td style='{td}'><strong>{objeto}</strong><br><small>{local}"
                f"{' · Pregão ' + op.numero_compra if op.numero_compra else ''} · {len(itens)} item(ns)</small></td>"
                f"<td style='{td}'>{nomes}</td><td style='{td};white-space:nowrap'>{fones}</td></tr>")
        extra = f"<p>E mais {len(grupos) - 60} licitação(ões) no Bidfy.</p>" if len(grupos) > 60 else ""
        html = _template_base(
            "<h2 style='color:#14532d;margin-top:0'>RELATÓRIO DE LEADS - LICITAÇÕES</h2>"
            f"<p>{cliente.nome}, estas são as licitações homologadas de <strong>{seg:%d/%m/%Y} a {dom:%d/%m/%Y}</strong> "
            f"com produtos do seu ramo: {len(grupos)} licitação(ões), {len(ops)} item(ns). "
            "As empresas vencedoras são potenciais compradoras dos seus produtos.</p>"
            "<table style='width:100%;border-collapse:collapse;font-size:13px'>"
            "<tr><th align='left'>Licitação / objeto</th><th align='left'>Vencedor</th><th align='left'>Telefone</th></tr>"
            + "".join(linhas) + "</table>" + extra +
            f"<p><a href='{BASE_URL}/prospeccao/leads?semana={seg:%Y-%m-%d}'>Ver o relatório completo e baixar o Excel no Bidfy</a></p>")
        if _enviar(destinos, "RELATÓRIO DE LEADS - LICITAÇÕES", html):
            enviados += 1
            agora = datetime.utcnow()
            for op in ops:
                op.avisada_em = op.avisada_em or agora
            db.session.commit()
    return enviados


def _brl(v):
    if v is None:
        return "—"
    return "R$ " + f"{float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


if __name__ == "__main__":
    from app import create_app
    app = create_app()
    with app.app_context():
        rodar_prospeccao()
