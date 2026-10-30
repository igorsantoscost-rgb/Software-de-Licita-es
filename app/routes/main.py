from flask import Blueprint, render_template, redirect, url_for, request, flash, session, g
import re
import difflib
from flask_login import login_required, current_user
from app.models import Licitacao, Cliente, User, STATUS_CHOICES, PalavraChaveCliente, ItemLicitacao, FavoritoLicitacao
from app import db, bcrypt
from app.capag import UFS, normalizar
from datetime import datetime, date, timedelta
import calendar

main_bp = Blueprint("main", __name__)


# Status "ativos" (em andamento) — prioridade de exibição no topo do painel geral ("Todos")
STATUS_ATIVOS = ["agendada", "em disputa", "em julgamento", "em habilitacao"]
# Ordem de prioridade para exibição no painel geral: ativos primeiro (nesta ordem),
# os demais status (homologada, revogada, cancelada, encerrada) vao para o final
# (fallback 99 em _ORDEM_STATUS.get), ordenados entre si por data da disputa.
_ORDEM_STATUS = {s: i for i, s in enumerate(STATUS_ATIVOS)}

# Status que, uma vez atingidos, tiram a licitacao do calendario (ela continua
# disponivel no painel, so nao faz mais sentido ocupar espaco na agenda).
STATUS_OCULTOS_CALENDARIO = ["revogada", "cancelada", "sem participacao", "interesse nao manifestado", "encerrada"]
STATUS_ANDAMENTO = ["em disputa", "em julgamento", "em habilitacao"]


def _em_andamento(busca=""):
    """Licitacoes ja disputadas e ainda em andamento (disputa, julgamento, habilitacao),
    agrupadas por status, para a faixa abaixo do calendario."""
    q = Licitacao.query.filter(Licitacao.status.in_(STATUS_ANDAMENTO))
    if not current_user.is_assessor():
        q = q.filter(Licitacao.cliente_id == current_user.cliente_id)
    lics = filtrar_por_busca(q.order_by(Licitacao.data_disputa.asc()).all(), busca)
    return [(s, [l for l in lics if l.status == s]) for s in STATUS_ANDAMENTO]



# Cards de contagem (painel e calendario)
MESES_NOME = ["", "janeiro", "fevereiro", "mar\u00e7o", "abril", "maio", "junho", "julho",
              "agosto", "setembro", "outubro", "novembro", "dezembro"]


def _por_cliente(lics, url_cliente):
    """[{nome, cor, n, url}] agrupando as licitacoes por cliente (mais licitacoes primeiro)."""
    grupos = {}
    for l in lics:
        g = grupos.setdefault(l.cliente_id, {"cliente": l.cliente, "n": 0})
        g["n"] += 1
    saida = []
    for g in grupos.values():
        c = g["cliente"]
        saida.append({"nome": c.nome if c else "-", "cor": (c.cor if c else None),
                      "n": g["n"], "url": url_cliente(c) if c else None})
    saida.sort(key=lambda x: (-x["n"], normalizar(x["nome"])))
    return saida


def _cards_painel():
    """Cards Agendadas / Em andamento do topo do painel.
    Assessor: todas, com a divisao por cliente. Cliente: so as dele."""
    q = Licitacao.query.filter(Licitacao.status.in_(["agendada"] + STATUS_ANDAMENTO))
    assessor = current_user.is_assessor()
    if not assessor:
        q = q.filter(Licitacao.cliente_id == current_user.cliente_id)
    lics = q.all()
    rotulos = {"em disputa": "disputa", "em julgamento": "julgamento", "em habilitacao": "habilita\u00e7\u00e3o"}
    hoje = date.today()
    cards = []
    for chave, titulo, status in (("agendada", "Agendadas", ["agendada"]),
                                  ("andamento", "Em andamento", STATUS_ANDAMENTO)):
        grupo = [l for l in lics if l.status in status]
        if chave == "andamento":
            sub = " \u00b7 ".join(f"{sum(1 for l in grupo if l.status == s)} {rotulos[s]}" for s in STATUS_ANDAMENTO)
        else:
            semana = sum(1 for l in grupo if l.data_disputa and hoje <= l.data_disputa.date() <= hoje + timedelta(days=7))
            sub = f"{semana} nos pr\u00f3ximos 7 dias"
        cards.append({
            "chave": chave, "titulo": titulo, "total": len(grupo), "sub": sub,
            "url": url_for("main.painel", status=chave, cliente_id="todos"),
            "clientes": _por_cliente(grupo, lambda c, k=chave: url_for("main.painel", status=k, cliente_id=c.id))
                        if assessor else None,
        })
    return cards


def _card_calendario(lics, titulo, url_cliente):
    """Card de total do periodo no topo do calendario (mesmo criterio do painel)."""
    assessor = current_user.is_assessor()
    return [{
        "chave": "periodo", "titulo": titulo, "total": len(lics),
        "sub": (f"{len({l.cliente_id for l in lics})} cliente(s)" if assessor else "suas disputas agendadas"),
        "url": None,
        "clientes": _por_cliente(lics, url_cliente) if assessor else None,
    }]


# Colunas clicaveis do painel -> funcao que extrai a chave de ordenacao de cada licitacao
_ORDENADORES_COLUNA = {
    "pregao": lambda l: (l.numero_pregao or "").strip().lower(),
    "orgao": lambda l: (l.orgao_licitante or "").strip().lower(),
    "portal": lambda l: (l.portal or "").strip().lower(),
    "data": lambda l: l.data_disputa or datetime.max,
    "status": lambda l: (l.status or "").strip().lower(),
    "cliente": lambda l: ((l.cliente.nome or "").strip().lower() if l.cliente else "", l.data_disputa or datetime.max),
}


def ids_favoritos():
    """Ids das licitacoes que o usuario logado marcou com estrela."""
    return {f.licitacao_id for f in FavoritoLicitacao.query.filter_by(user_id=current_user.id)}


def ids_favoritos_cliente():
    """Ids das licitacoes que o PROPRIO cliente (algum usuario dele) marcou com estrela."""
    from app.models import User
    q = (db.session.query(FavoritoLicitacao.licitacao_id)
         .join(User, User.id == FavoritoLicitacao.user_id)
         .join(Licitacao, Licitacao.id == FavoritoLicitacao.licitacao_id)
         .filter(User.perfil == "cliente", User.cliente_id == Licitacao.cliente_id))
    return {r[0] for r in q.all()}


def _lembrar_tela(url, rotulo):
    """Guarda a tela de lista (com filtros, busca e ordenacao) de onde o
    usuario abriu a licitacao, para o botao "Voltar" do detalhe trazer de
    volta exatamente para o mesmo lugar."""
    session["voltar_url"] = url
    session["voltar_rotulo"] = rotulo


# ─── Busca por texto (painel e calendario) ───────────────────────────────────

_ACENTOS_DE = "áàâãäéèêëíìîïóòôõöúùûüçñÁÀÂÃÄÉÈÊËÍÌÎÏÓÒÔÕÖÚÙÛÜÇÑ"
_ACENTOS_PARA = "aaaaaeeeeiiiiooooouuuucnaaaaaeeeeiiiiooooouuuucn"


def _texto_busca(l):
    """Tudo o que a busca enxerga numa licitacao (sem acento, minusculo)."""
    partes = [
        l.orgao_licitante, l.objeto, l.portal, l.numero_pregao, l.uasg,
        l.municipio, l.uf, l.status, l.codigo_busca, l.link_edital,
        l.motivo_encerramento,
        l.cliente.nome if l.cliente else "",
        l.data_disputa.strftime("%d/%m/%Y %H:%M") if l.data_disputa else "",
    ]
    return normalizar(" ".join(p for p in partes if p))


def _ids_por_item(ids, palavra):
    """Licitacoes (entre ids) com algum item cuja descricao/lote contem a palavra."""
    if not ids:
        return set()
    from sqlalchemy import func, or_
    padrao = f"%{palavra}%"
    try:
        sem_acento = lambda col: func.translate(func.lower(col), _ACENTOS_DE, _ACENTOS_PARA)
        linhas = (db.session.query(ItemLicitacao.licitacao_id)
                  .filter(ItemLicitacao.licitacao_id.in_(ids))
                  .filter(or_(sem_acento(ItemLicitacao.descricao).like(padrao),
                              sem_acento(ItemLicitacao.lote_grupo).like(padrao)))
                  .distinct().all())
    except Exception:
        # banco sem translate() (ex: sqlite de teste): busca simples
        db.session.rollback()
        linhas = (db.session.query(ItemLicitacao.licitacao_id)
                  .filter(ItemLicitacao.licitacao_id.in_(ids))
                  .filter(ItemLicitacao.descricao.ilike(padrao))
                  .distinct().all())
    return {r[0] for r in linhas}


def filtrar_por_busca(lics, busca):
    """Mantem so as licitacoes que tem TODAS as palavras buscadas em algum
    campo (orgao, objeto, portal, numero, UASG, municipio, status, cliente,
    data) ou na descricao de algum item."""
    palavras = normalizar(busca).split() if busca else []
    if not palavras:
        return lics
    ids = [l.id for l in lics]
    textos = {l.id: _texto_busca(l) for l in lics}
    por_item = {}
    resultado = []
    for l in lics:
        ok = True
        for p in palavras:
            if p in textos[l.id]:
                continue
            if p not in por_item:
                por_item[p] = _ids_por_item(ids, p)
            if l.id not in por_item[p]:
                ok = False
                break
        if ok:
            resultado.append(l)
    return resultado


def sugerir_busca(busca, lics):
    """Busca sem resultado: troca cada palavra que nao existe por a mais parecida
    que aparece nas licitacoes ou na descricao dos itens (erro de digitacao).
    Ex: "tavesseiro" -> "travesseiro". Devolve None se nao houver sugestao."""
    palavras = normalizar(busca).split() if busca else []
    if not palavras or not lics:
        return None
    vocab = set()
    for l in lics:
        vocab.update(re.findall(r"[a-z0-9]{3,}", _texto_busca(l)))
    ids = [l.id for l in lics]
    for (desc, lote) in (db.session.query(ItemLicitacao.descricao, ItemLicitacao.lote_grupo)
                         .filter(ItemLicitacao.licitacao_id.in_(ids)).all()):
        vocab.update(re.findall(r"[a-z0-9]{3,}", normalizar((desc or "") + " " + (lote or ""))))
    vocab = list(vocab)
    novas, mudou = [], False
    for p in palavras:
        if any(p in w for w in vocab):
            novas.append(p)
            continue
        parecida = difflib.get_close_matches(p, vocab, n=1, cutoff=0.8)
        if parecida:
            novas.append(parecida[0])
            mudou = True
        else:
            novas.append(p)
    return " ".join(novas) if mudou else None


def _corrigir_busca_calendario(busca):
    """Calendario: se a busca nao acha nada, tenta a palavra parecida."""
    if not busca:
        return busca
    q = Licitacao.query.filter(Licitacao.data_disputa.isnot(None))
    if not current_user.is_assessor():
        q = q.filter(Licitacao.cliente_id == current_user.cliente_id)
    base = q.all()
    if filtrar_por_busca(base, busca):
        return busca
    sugestao = sugerir_busca(busca, base)
    if sugestao:
        g.busca_digitada = busca
        return sugestao
    return busca


def _licitacoes_do_usuario(status_filtro=None, cliente_filtro=None, sort_coluna=None, sort_dir="asc"):
    q = Licitacao.query
    if not current_user.is_assessor():
        # Cliente sempre vê apenas as próprias licitações
        q = q.filter_by(cliente_id=current_user.cliente_id)
    elif cliente_filtro and cliente_filtro != "todos":
        # Assessor pode filtrar por um cliente específico
        q = q.filter_by(cliente_id=cliente_filtro)
    if status_filtro == "favoritas":
        q = q.filter(Licitacao.id.in_(
            db.session.query(FavoritoLicitacao.licitacao_id).filter_by(user_id=current_user.id)))
    elif status_filtro == "fav_cliente":
        q = q.filter(Licitacao.id.in_(ids_favoritos_cliente() or {0}))
    elif status_filtro == "finalizadas":
        q = q.filter(Licitacao.status.in_(("homologada", "encerrada")))
    elif status_filtro == "andamento":
        q = q.filter(Licitacao.status.in_(STATUS_ANDAMENTO))
    elif status_filtro and status_filtro != "todos":
        q = q.filter_by(status=status_filtro)
    # "Todos": nao filtra por status — toda licitacao continua aparecendo no
    # painel mesmo apos mudar de status; a ordenacao abaixo e que joga as
    # concluidas (homologada/revogada/cancelada/encerrada) pro final da lista.
    lics = q.order_by(Licitacao.data_disputa.asc()).all()

    if sort_coluna in _ORDENADORES_COLUNA:
        # Usuario clicou num titulo de coluna: essa ordenacao manda
        lics.sort(key=_ORDENADORES_COLUNA[sort_coluna], reverse=(sort_dir == "desc"))
    elif status_filtro in ("todos", "favoritas", "fav_cliente") or not status_filtro:
        # Padrao: ordena por prioridade de status, depois por data
        lics.sort(key=lambda l: (_ORDEM_STATUS.get(l.status, 99), l.data_disputa or datetime.max))
    return lics


@main_bp.route("/painel")
@login_required
def painel():
    status_arg = request.args.get("status")
    cliente_arg = request.args.get("cliente_id")
    if status_arg is None and cliente_arg is None and current_user.is_assessor():
        # Nenhum filtro veio na URL (ex: voltou do detalhe de uma licitacao):
        # usa o ultimo filtro que o assessor selecionou nesta sessao, se houver.
        status_filtro = session.get("painel_status_filtro", "todos")
        cliente_filtro = session.get("painel_cliente_filtro", "todos")
    else:
        status_filtro = status_arg or "todos"
        cliente_filtro = cliente_arg or "todos"
    if current_user.is_assessor():
        session["painel_status_filtro"] = status_filtro
        session["painel_cliente_filtro"] = cliente_filtro

    sort_coluna = request.args.get("sort", "")
    sort_dir = request.args.get("dir", "asc")
    if sort_dir not in ("asc", "desc"):
        sort_dir = "asc"

    busca = (request.args.get("busca") or "").strip()
    licitacoes = _licitacoes_do_usuario(status_filtro, cliente_filtro, sort_coluna, sort_dir)
    base_busca = licitacoes
    licitacoes = filtrar_por_busca(licitacoes, busca)
    if busca and not licitacoes:
        sugestao = sugerir_busca(busca, base_busca)
        if sugestao:
            g.busca_digitada, busca = busca, sugestao
            licitacoes = filtrar_por_busca(base_busca, busca)
    _lembrar_tela(url_for("main.painel", status=status_filtro, cliente_id=cliente_filtro,
                          sort=sort_coluna or None, dir=sort_dir if sort_coluna else None,
                          busca=busca or None), "Painel")
    clientes = Cliente.query.order_by(Cliente.nome).all() if current_user.is_assessor() else []
    return render_template(
        "painel.html",
        licitacoes=licitacoes,
        status_choices=STATUS_CHOICES,
        status_filtro=status_filtro,
        cliente_filtro=cliente_filtro,
        clientes=clientes,
        sort_coluna=sort_coluna,
        sort_dir=sort_dir,
        busca=busca,
        fav_ids=ids_favoritos(),
        fav_cliente_ids=ids_favoritos_cliente() if current_user.is_assessor() else set(),
        metricas_painel=_metricas_painel(licitacoes, status_filtro),
        sem_acesso_ids=_sem_acesso_portal(licitacoes) if current_user.is_assessor() else set(),
        consultas_pendentes=_consultas_pendentes_cliente(),
        cards_resumo=_cards_painel(),
    )


def _sem_acesso_portal(licitacoes):
    """Licitacoes agendadas em portal onde o cliente nao tem acesso ativo."""
    from app.models import AcessoPortal
    from app.portais import chave_portal
    hoje = date.today()
    ativos = {(a.cliente_id, chave_portal(a.portal)) for a in AcessoPortal.query.all() if a.ativo_em(hoje)}
    return {l.id for l in licitacoes
            if l.status == "agendada" and l.portal and (l.cliente_id, chave_portal(l.portal)) not in ativos}


def _consultas_pendentes_cliente():
    """Consultas de compra de acesso aguardando a resposta do cliente logado."""
    if current_user.is_assessor() or not current_user.cliente_id:
        return []
    from app.models import SolicitacaoAcessoPortal
    return SolicitacaoAcessoPortal.query.filter_by(cliente_id=current_user.cliente_id, status="pendente").all()


def _metricas_painel(licitacoes, status_filtro):
    """Indicadores consolidados quando o painel mostra licitacoes finalizadas."""
    if status_filtro not in ("finalizadas", "homologada", "encerrada"):
        return None
    from app.resultados import metricas
    itens = [i for l in licitacoes for i in l.itens]
    m = metricas(itens)
    m["licitacoes"] = len(licitacoes)
    m["valor_homologado"] = sum(float(l.valor_homologado or 0) for l in licitacoes if l.status == "homologada")
    return m


def _resultados_busca_calendario(busca):
    """Com busca ativa, lista TODAS as licitacoes que batem (de qualquer mes),
    para o usuario achar e pular direto para a data certa no calendario."""
    if not busca:
        return None
    q = Licitacao.query.filter(Licitacao.data_disputa.isnot(None))
    if not current_user.is_assessor():
        q = q.filter(Licitacao.cliente_id == current_user.cliente_id)
    return filtrar_por_busca(q.order_by(Licitacao.data_disputa.asc()).all(), busca)


@main_bp.route("/calendario")
@login_required
def calendario():
    # O calendario abre sempre na visao semanal; a mensal so com mes/ano na URL
    # (o botao "Mensal" ja manda os dois).
    if "mes" not in request.args and "ano" not in request.args:
        return redirect(url_for("main.calendario_semana", busca=request.args.get("busca") or None))
    mes = request.args.get("mes", type=int, default=date.today().month)
    ano = request.args.get("ano", type=int, default=date.today().year)
    if mes < 1: mes, ano = 12, ano - 1
    if mes > 12: mes, ano = 1, ano + 1

    primeiro_dia = date(ano, mes, 1)
    ultimo_dia = date(ano, mes, calendar.monthrange(ano, mes)[1])

    q = Licitacao.query.filter(
        Licitacao.data_disputa >= datetime(ano, mes, 1),
        Licitacao.data_disputa <= datetime(ano, mes, ultimo_dia.day, 23, 59, 59),
        Licitacao.status == "agendada",  # no calendario so as disputas agendadas
    )
    if not current_user.is_assessor():
        q = q.filter(Licitacao.cliente_id == current_user.cliente_id)
    busca = _corrigir_busca_calendario((request.args.get("busca") or "").strip())
    licitacoes_mes = filtrar_por_busca(q.all(), busca)
    _lembrar_tela(url_for("main.calendario", mes=mes, ano=ano, busca=busca or None), "Calendário")
    resultados_busca = _resultados_busca_calendario(busca)

    eventos = {}
    for l in licitacoes_mes:
        d = l.data_disputa.date()
        eventos.setdefault(d, []).append(l)

    from app.feriados import feriados_entre
    feriados = feriados_entre(primeiro_dia, ultimo_dia)
    cards_resumo = _card_calendario(
        licitacoes_mes, "Licita\u00e7\u00f5es em " + MESES_NOME[mes],
        lambda c: url_for("main.calendario", mes=mes, ano=ano, busca=c.nome))

    calendar.setfirstweekday(6)  # 6 = domingo (calendar usa 0=segunda por padrao)
    semanas = calendar.monthcalendar(ano, mes)
    nomes_meses = [
        "", "Janeiro", "Fevereiro", "Marco", "Abril", "Maio", "Junho",
        "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"
    ]

    return render_template(
        "calendario.html",
        semanas=semanas,
        eventos=eventos,
        mes=mes,
        ano=ano,
        nome_mes=nomes_meses[mes],
        feriados=feriados,
        cards_resumo=cards_resumo,
        primeiro_dia=primeiro_dia,
        hoje=date.today(),
        busca=busca,
        resultados_busca=resultados_busca,
        em_andamento=_em_andamento(busca),
    )


@main_bp.route("/calendario/semana")
@login_required
def calendario_semana():
    inicio_str = request.args.get("inicio")
    if inicio_str:
        try:
            inicio = datetime.strptime(inicio_str, "%Y-%m-%d").date()
        except ValueError:
            inicio = date.today()
    else:
        inicio = date.today()

    # Dia clicado no calendario mensal (fica destacado na semana)
    dia_destaque = None
    try:
        dia_destaque = datetime.strptime(request.args.get("dia", ""), "%Y-%m-%d").date()
    except ValueError:
        pass

    # Semana de segunda a sexta. Domingo pula para a semana seguinte
    # (no calendario mensal o domingo abre a linha da semana).
    if inicio.weekday() == 6:
        inicio = inicio + timedelta(days=1)
    inicio = inicio - timedelta(days=inicio.weekday())
    fim = inicio + timedelta(days=4)
    fim_busca = inicio + timedelta(days=6)  # pega tambem disputas no sabado/domingo

    dias_semana = [inicio + timedelta(days=i) for i in range(5)]

    q = Licitacao.query.filter(
        Licitacao.data_disputa >= datetime(inicio.year, inicio.month, inicio.day),
        Licitacao.data_disputa <= datetime(fim_busca.year, fim_busca.month, fim_busca.day, 23, 59, 59),
        Licitacao.status == "agendada",  # no calendario so as disputas agendadas
    )
    if not current_user.is_assessor():
        q = q.filter(Licitacao.cliente_id == current_user.cliente_id)
    busca = _corrigir_busca_calendario((request.args.get("busca") or "").strip())
    licitacoes_semana = filtrar_por_busca(q.order_by(Licitacao.data_disputa.asc()).all(), busca)
    _lembrar_tela(url_for("main.calendario_semana", inicio=inicio.isoformat(),
                          dia=dia_destaque.isoformat() if dia_destaque else None,
                          busca=busca or None), "Calendário semanal")
    resultados_busca = _resultados_busca_calendario(busca)

    eventos = {}
    for l in licitacoes_semana:
        d = l.data_disputa.date()
        eventos.setdefault(d, []).append(l)

    from app.feriados import feriados_entre
    feriados = feriados_entre(inicio, fim)
    fim_de_semana = [l for l in licitacoes_semana if l.data_disputa.date() > fim]
    cards_resumo = _card_calendario(
        licitacoes_semana, "Licita\u00e7\u00f5es nesta semana",
        lambda c: url_for("main.calendario_semana", inicio=inicio.isoformat(), busca=c.nome))

    nomes_meses_curto = [
        "", "Jan", "Fev", "Mar", "Abr", "Mai", "Jun",
        "Jul", "Ago", "Set", "Out", "Nov", "Dez"
    ]

    return render_template(
        "calendario_semana.html",
        dias_semana=dias_semana,
        eventos=eventos,
        inicio=inicio,
        fim=fim,
        feriados=feriados,
        fim_de_semana=fim_de_semana,
        cards_resumo=cards_resumo,
        semana_anterior=inicio - timedelta(days=7),
        semana_seguinte=inicio + timedelta(days=7),
        nomes_meses_curto=nomes_meses_curto,
        hoje=date.today(),
        dia_destaque=dia_destaque,
        busca=busca,
        resultados_busca=resultados_busca,
        em_andamento=_em_andamento(busca),
    )


# ─── Gerenciar clientes (assessor) ───────────────────────────────────────────

@main_bp.route("/clientes")
@login_required
def clientes():
    if not current_user.is_assessor():
        return redirect(url_for("main.painel"))
    todos = Cliente.query.order_by(Cliente.nome).all()
    return render_template("clientes.html", clientes=todos)


@main_bp.route("/clientes/novo", methods=["GET", "POST"])
@login_required
def novo_cliente():
    if not current_user.is_assessor():
        return redirect(url_for("main.painel"))
    if request.method == "POST":
        nome = request.form.get("nome", "").strip()
        cnpj = request.form.get("cnpj", "").strip()
        email_user = request.form.get("email_usuario", "").strip().lower()
        senha_user = request.form.get("senha_usuario", "")
        nome_user = request.form.get("nome_usuario", "").strip()
        if not nome or not email_user or not senha_user:
            flash("Preencha todos os campos obrigatorios.", "erro")
            return render_template("form_cliente.html", ufs=UFS)
        cliente = Cliente(
            nome=nome,
            cnpj=cnpj,
            rua=request.form.get("rua", "").strip(),
            numero=request.form.get("numero", "").strip(),
            complemento=request.form.get("complemento", "").strip(),
            bairro=request.form.get("bairro", "").strip(),
            cidade=request.form.get("cidade", "").strip(),
            estado=request.form.get("estado", "").strip().upper(),
            cep=request.form.get("cep", "").strip(),
            nome_contato=request.form.get("nome_contato", "").strip(),
            cargo_contato=request.form.get("cargo_contato", "").strip(),
            telefone_fixo=request.form.get("telefone_fixo", "").strip(),
            telefone_wpp=request.form.get("telefone_wpp", "").strip(),
            email_contato=request.form.get("email_contato", "").strip(),
            email_financeiro=request.form.get("email_financeiro", "").strip(),
            cor=request.form.get("cor", "").strip() or None,
        )
        db.session.add(cliente)
        db.session.flush()
        user = User(
            nome=nome_user,
            email=email_user,
            senha=bcrypt.generate_password_hash(senha_user).decode("utf-8"),
            perfil="cliente",
            cliente_id=cliente.id,
        )
        db.session.add(user)
        db.session.commit()
        flash("Cliente criado com sucesso.", "ok")
        return redirect(url_for("main.clientes"))
    return render_template("form_cliente.html", ufs=UFS)


@main_bp.route("/clientes/<int:id>/editar", methods=["GET", "POST"])
@login_required
def editar_cliente(id):
    if not current_user.is_assessor():
        return redirect(url_for("main.painel"))
    cliente = Cliente.query.get_or_404(id)
    if request.method == "POST":
        nome = request.form.get("nome", "").strip()
        if not nome:
            flash("O nome da empresa é obrigatório.", "erro")
            return _tela_editar_cliente(cliente)
        cliente.nome = nome
        cliente.cnpj = request.form.get("cnpj", "").strip()
        cliente.rua = request.form.get("rua", "").strip()
        cliente.numero = request.form.get("numero", "").strip()
        cliente.complemento = request.form.get("complemento", "").strip()
        cliente.bairro = request.form.get("bairro", "").strip()
        cliente.cidade = request.form.get("cidade", "").strip()
        cliente.estado = request.form.get("estado", "").strip().upper()
        cliente.cep = request.form.get("cep", "").strip()
        cliente.nome_contato = request.form.get("nome_contato", "").strip()
        cliente.cargo_contato = request.form.get("cargo_contato", "").strip()
        cliente.telefone_fixo = request.form.get("telefone_fixo", "").strip()
        cliente.telefone_wpp = request.form.get("telefone_wpp", "").strip()
        cliente.email_contato = request.form.get("email_contato", "").strip()
        cliente.email_financeiro = request.form.get("email_financeiro", "").strip()
        cliente.cor = request.form.get("cor", "").strip() or None
        taxa_str = request.form.get("taxa_consultoria", "").strip().replace(",", ".")
        if taxa_str:
            try:
                cliente.taxa_consultoria = float(taxa_str)
            except ValueError:
                pass
        db.session.commit()
        flash("Cliente atualizado.", "ok")
        return redirect(url_for("main.clientes"))
    return _tela_editar_cliente(cliente)


def _ufs_brasil():
    from app.models import UFS_BRASIL
    return sorted(UFS_BRASIL)


def _tela_editar_cliente(cliente):
    """Cadastro do cliente com as abas Dados, Filtros de participacao e Portais."""
    from app.models import (CobrancaPortal, SolicitacaoAcessoPortal, FORMAS_PAGAMENTO_PORTAL, Fatura)
    from app.portais import portais_disponiveis
    ids_acessos = [a.id for a in cliente.acessos_portal]
    solicitacoes = (SolicitacaoAcessoPortal.query.filter_by(cliente_id=cliente.id)
                    .order_by(SolicitacaoAcessoPortal.criado_em.desc()).limit(50).all())
    # Cobrancas: tambem as de acessos ja excluidos, pelas faturas do cliente
    ids_faturas = [f.id for f in Fatura.query.filter_by(cliente_id=cliente.id).all()]
    q = CobrancaPortal.query
    if ids_faturas:
        q = q.filter(db.or_(CobrancaPortal.acesso_id.in_(ids_acessos or [0]),
                            CobrancaPortal.fatura_id.in_(ids_faturas)))
    else:
        q = q.filter(CobrancaPortal.acesso_id.in_(ids_acessos or [0]))
    cobrancas = q.order_by(CobrancaPortal.criado_em.desc()).limit(60).all()
    faturas = {f.id: f for f in Fatura.query.filter(Fatura.id.in_([c.fatura_id for c in cobrancas if c.fatura_id] or [0])).all()}
    return render_template("form_cliente_editar.html", cliente=cliente, ufs=UFS, ufs_brasil=_ufs_brasil(),
                           portais=portais_disponiveis(), formas_pagamento=FORMAS_PAGAMENTO_PORTAL,
                           solicitacoes=solicitacoes, cobrancas=cobrancas, faturas_cobranca=faturas,
                           hoje=date.today())


@main_bp.route("/clientes/<int:id>/palavras-chave/adicionar", methods=["POST"])
@login_required
def adicionar_palavra_chave(id):
    if not current_user.is_assessor():
        return redirect(url_for("main.painel"))
    cliente = Cliente.query.get_or_404(id)
    texto = request.form.get("palavra", "").strip()
    tipo = "exclusao" if request.form.get("tipo") == "exclusao" else "inclusao"
    if texto:
        # Permite colar varias palavras separadas por virgula de uma vez
        novas = [p.strip() for p in texto.split(",") if p.strip()]
        lista = cliente.palavras_exclusao if tipo == "exclusao" else cliente.palavras_chave
        existentes = {p.palavra.lower() for p in lista}
        for p in novas:
            if p.lower() not in existentes:
                db.session.add(PalavraChaveCliente(cliente_id=cliente.id, palavra=p[:150], tipo=tipo))
                existentes.add(p.lower())
        db.session.commit()
        flash("Palavra(s) de exclusão adicionada(s)." if tipo == "exclusao"
              else "Palavra(s)-chave adicionada(s).", "ok")
    return redirect(url_for("main.editar_cliente", id=id, _anchor="filtros"))


@main_bp.route("/clientes/<int:id>/filtros", methods=["POST"])
@login_required
def salvar_filtros_cliente(id):
    """Regras de participacao: esferas/estados, ME/EPP, CAPAG e valor minimo."""
    import json
    from app.models import ESFERAS, UFS_BRASIL
    if not current_user.is_assessor():
        return redirect(url_for("main.painel"))
    cliente = Cliente.query.get_or_404(id)
    regras = {}
    for e in ESFERAS:
        participa = request.form.get(f"{e}_participa") == "1"
        ufs = []
        if participa and request.form.get(f"{e}_abrangencia") == "estados":
            ufs = sorted(u for u in request.form.getlist(f"{e}_ufs") if u in UFS_BRASIL)
            if not ufs:
                flash(f"Escolha ao menos um estado para a esfera {e} (ou marque Todo o Brasil). "
                      "Deixei essa esfera em todo o Brasil.", "erro")
        regras[e] = {"participa": participa, "ufs": ufs}
    cliente.regras_esfera = json.dumps(regras)
    cliente.bloquear_exclusiva_me = request.form.get("bloquear_exclusiva_me") == "1"
    cliente.capag_bloqueadas = ",".join(n for n in request.form.getlist("capag_bloqueadas")
                                        if n in ("A+", "A", "B+", "B", "C", "D")) or None
    cliente.valor_minimo_estimado = _valor_reais(request.form.get("valor_minimo_estimado"))
    db.session.commit()
    flash("Filtros de participação salvos.", "ok")
    return redirect(url_for("main.editar_cliente", id=id, _anchor="filtros"))


@main_bp.route("/clientes/palavras-chave/<int:palavra_id>/excluir", methods=["POST"])
@login_required
def excluir_palavra_chave(palavra_id):
    if not current_user.is_assessor():
        return redirect(url_for("main.painel"))
    palavra = PalavraChaveCliente.query.get_or_404(palavra_id)
    cliente_id = palavra.cliente_id
    db.session.delete(palavra)
    db.session.commit()
    flash("Palavra removida.", "ok")
    return redirect(url_for("main.editar_cliente", id=cliente_id, _anchor="filtros"))


# ─── Gestão de Assessores (só master) ────────────────────────────────────────

@main_bp.route("/assessores")
@login_required
def assessores():
    if not current_user.is_master():
        return redirect(url_for("main.painel"))
    todos = User.query.filter_by(perfil="assessor").order_by(User.nome).all()
    return render_template("assessores.html", assessores=todos)


@main_bp.route("/assessores/novo", methods=["GET", "POST"])
@login_required
def novo_assessor():
    if not current_user.is_master():
        return redirect(url_for("main.painel"))
    clientes = Cliente.query.order_by(Cliente.nome).all()
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        nome = request.form.get("nome", "").strip()
        email = request.form.get("email", "").strip().lower() or None
        senha = request.form.get("senha", "")
        telefone = request.form.get("telefone", "").strip()
        endereco = request.form.get("endereco", "").strip()
        clientes_ids = request.form.getlist("clientes_ids")
        if not username or not nome or not senha:
            flash("Nome de usuário, nome e senha são obrigatórios.", "erro")
            return render_template("form_assessor.html", clientes=clientes)
        if User.query.filter(db.func.lower(User.username) == username.lower()).first():
            flash("Esse nome de usuário já está em uso.", "erro")
            return render_template("form_assessor.html", clientes=clientes)
        assessor = User(
            username=username,
            nome=nome,
            email=email,
            senha=bcrypt.generate_password_hash(senha).decode("utf-8"),
            perfil="assessor",
            telefone=telefone,
            endereco=endereco,
        )
        db.session.add(assessor)
        db.session.flush()
        for cid in clientes_ids:
            c = Cliente.query.get(int(cid))
            if c:
                assessor.clientes_atendidos.append(c)
        db.session.commit()
        flash(f"Assessor {nome} criado com sucesso.", "ok")
        return redirect(url_for("main.assessores"))
    return render_template("form_assessor.html", clientes=clientes, assessor=None)


@main_bp.route("/assessores/<int:id>/editar", methods=["GET", "POST"])
@login_required
def editar_assessor(id):
    if not current_user.is_master():
        return redirect(url_for("main.painel"))
    assessor = User.query.get_or_404(id)
    clientes = Cliente.query.order_by(Cliente.nome).all()
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        nome = request.form.get("nome", "").strip()
        email = request.form.get("email", "").strip().lower() or None
        telefone = request.form.get("telefone", "").strip()
        endereco = request.form.get("endereco", "").strip()
        nova_senha = request.form.get("senha", "").strip()
        clientes_ids = request.form.getlist("clientes_ids")
        if not username or not nome:
            flash("Nome de usuário e nome são obrigatórios.", "erro")
            return render_template("form_assessor.html", clientes=clientes, assessor=assessor)
        conflito = User.query.filter(
            db.func.lower(User.username) == username.lower(),
            User.id != id
        ).first()
        if conflito:
            flash("Esse nome de usuário já está em uso.", "erro")
            return render_template("form_assessor.html", clientes=clientes, assessor=assessor)
        assessor.username = username
        assessor.nome = nome
        assessor.email = email
        assessor.telefone = telefone
        assessor.endereco = endereco
        if nova_senha:
            assessor.senha = bcrypt.generate_password_hash(nova_senha).decode("utf-8")
        # Atualiza vinculos
        assessor.clientes_atendidos = []
        for cid in clientes_ids:
            c = Cliente.query.get(int(cid))
            if c:
                assessor.clientes_atendidos.append(c)
        db.session.commit()
        flash("Assessor atualizado.", "ok")
        return redirect(url_for("main.assessores"))
    return render_template("form_assessor.html", clientes=clientes, assessor=assessor)


# ─── Gestão de Acessos (só master) ───────────────────────────────────────────

@main_bp.route("/gestao-acessos")
@login_required
def gestao_acessos():
    if not current_user.is_master():
        return redirect(url_for("main.painel"))
    usuarios = User.query.order_by(User.perfil, User.nome).all()
    return render_template("gestao_acessos.html", usuarios=usuarios)


@main_bp.route("/gestao-acessos/<int:id>/editar", methods=["GET", "POST"])
@login_required
def editar_acesso(id):
    if not current_user.is_master():
        return redirect(url_for("main.painel"))
    usuario = User.query.get_or_404(id)
    clientes = Cliente.query.order_by(Cliente.nome).all()
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        nome = request.form.get("nome", "").strip()
        email = request.form.get("email", "").strip().lower() or None
        nova_senha = request.form.get("senha", "").strip()
        perfil = request.form.get("perfil", usuario.perfil)
        cliente_id = request.form.get("cliente_id") or None
        if not username or not nome:
            flash("Nome de usuário e nome são obrigatórios.", "erro")
            return render_template("form_acesso.html", usuario=usuario, clientes=clientes)
        conflito = User.query.filter(
            db.func.lower(User.username) == username.lower(),
            User.id != id
        ).first()
        if conflito:
            flash("Esse nome de usuário já está em uso.", "erro")
            return render_template("form_acesso.html", usuario=usuario, clientes=clientes)
        usuario.username = username
        usuario.nome = nome
        usuario.email = email
        usuario.perfil = perfil
        usuario.cliente_id = int(cliente_id) if cliente_id else None
        if nova_senha:
            usuario.senha = bcrypt.generate_password_hash(nova_senha).decode("utf-8")
        db.session.commit()
        flash("Acesso atualizado.", "ok")
        return redirect(url_for("main.gestao_acessos"))
    return render_template("form_acesso.html", usuario=usuario, clientes=clientes)


def _valor_reais(txt):
    """'20.000,00' / '20000' / 'R$ 20 mil' -> 20000.0; vazio ou zero -> None."""
    t = (txt or "").strip().lower().replace("r$", "").replace(" ", "")
    mult = 1
    if t.endswith("mil"):
        t, mult = t[:-3], 1000
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    elif t.count(".") == 1 and len(t.split(".")[1]) == 3:
        t = t.replace(".", "")  # 20.000 = vinte mil
    try:
        v = round(float(t) * mult, 2)
    except ValueError:
        return None
    return v if v > 0 else None
