"""
Prospeccao diaria de licitacoes HOMOLOGADAS no PNCP, pelas palavras-chave
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


def cabecalho_compra(cnpj, ano, seq):
    return _get(f"{API}/orgaos/{cnpj}/compras/{ano}/{seq}") or {}


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


def _combina(pares, texto):
    from app.classificador_cliente import _normalizar
    t = _normalizar(texto)
    return [p for p, rx in pares if rx.search(t)]


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
        alvo = [(c, _combina(pares, texto)) for c, pares in clientes]
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
                orgao = (cab.get("orgaoEntidade") or {}) if cab else {}
                unidade = (cab.get("unidadeOrgao") or {}) if cab else {}
                op = OportunidadeHomologada(
                    cliente_id=cliente.id, cnpj_orgao=cnpj, ano=ano, sequencial=seq,
                    numero_item=int(numero),
                    orgao=(orgao.get("razaoSocial") or compra.titulo or "")[:300],
                    uf=(unidade.get("ufSigla") or "")[:2] or None,
                    municipio=(unidade.get("municipioNome") or "")[:150] or None,
                    numero_compra=(f"{cab.get('numeroCompra')}/{cab.get('anoCompra')}"
                                   if cab and cab.get("numeroCompra") else None),
                    modalidade=(cab.get("modalidadeNome") or "")[:80] if cab else None,
                    objeto=(cab.get("objetoCompra") if cab else None),
                    srp=bool(cab.get("srp")) if cab and cab.get("srp") is not None else None,
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
                    data_resultado=_data(res.get("dataResultado")),
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
            db.session.add(CompraMonitorada(cnpj=ids[0], ano=ids[1], sequencial=ids[2],
                                            titulo=(it.get("orgao_nome") or it.get("title") or "")[:300],
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

    avisados = enviar_avisos() if enviar_email else 0
    if avisados:
        log(f"Prospecção: e-mail enviado para {avisados} cliente(s).")
    return {"clientes": len(clientes), "buscados": buscados, "verificados": verificados, "novas": novas}


# ─── e-mail diario ───────────────────────────────────────────────────────────

def enviar_avisos():
    """Um e-mail por cliente com as oportunidades ainda nao avisadas."""
    from app.email_service import _enviar, _template_base, _emails_assessores_do_cliente, BASE_URL
    enviados = 0
    clientes = (db.session.query(OportunidadeHomologada.cliente_id)
                .filter(OportunidadeHomologada.avisada_em.is_(None)).distinct().all())
    for (cid,) in clientes:
        cliente = Cliente.query.get(cid)
        ops = (OportunidadeHomologada.query
               .filter_by(cliente_id=cid, avisada_em=None)
               .order_by(OportunidadeHomologada.data_resultado.desc().nullslast()).all())
        if not cliente or not ops:
            continue
        linhas = []
        for op in ops[:40]:
            total = op.valor_total_homologado
            linhas.append(
                "<tr>"
                f"<td style='padding:6px;border-bottom:1px solid #eee'>{(op.orgao or '')[:60]}"
                f"{' / ' + op.uf if op.uf else ''}<br><small>{(op.descricao_item or '')[:110]}</small></td>"
                f"<td style='padding:6px;border-bottom:1px solid #eee'>{(op.fornecedor_nome or op.fornecedor_cnpj)[:50]}"
                f"{'<br><small>' + op.fornecedor_telefone + '</small>' if op.fornecedor_telefone else ''}</td>"
                f"<td style='padding:6px;border-bottom:1px solid #eee;white-space:nowrap'>"
                f"{_brl(total) if total else _brl(op.valor_unitario_homologado)}"
                f"{'<br><small>ata até ' + op.ata_vigencia_fim.strftime('%d/%m/%Y') + '</small>' if op.ata_vigente else ''}</td>"
                "</tr>")
        extra = f"<p>E mais {len(ops) - 40} na tela.</p>" if len(ops) > 40 else ""
        html = _template_base(
            f"<h2 style='color:#14532d;margin-top:0'>Licitações homologadas — {cliente.nome}</h2>"
            f"<p>{len(ops)} item(ns) homologado(s) novo(s) com as palavras-chave do cliente.</p>"
            "<table style='width:100%;border-collapse:collapse;font-size:13px'>"
            "<tr><th align='left'>Órgão / item</th><th align='left'>Vencedor</th><th align='left'>Valor</th></tr>"
            + "".join(linhas) + "</table>" + extra +
            f"<p><a href='{BASE_URL}/prospeccao/?cliente_id={cid}'>Abrir no Bidfy</a></p>")
        if _enviar(_emails_assessores_do_cliente(cliente),
                   f"Bidfy: {len(ops)} homologada(s) nova(s) — {cliente.nome}", html):
            enviados += 1
        agora = datetime.utcnow()
        for op in ops:
            op.avisada_em = agora
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
