"""
Guia Gestao (versao de teste, visivel so para o Igor).

Dashboard de resultados por cliente: oportunidades disponibilizadas,
participadas, sem participacao, vencidas, valor homologado e valor empenhado,
no periodo escolhido (meses) e desde o inicio dos trabalhos.

Regras de contagem (cada licitacao entra no mes da data da disputa; sem data,
no mes em que foi cadastrada):
- disponibilizada: toda licitacao cadastrada para o cliente;
- participada: chegou a disputa (em disputa, em julgamento, em habilitacao,
  homologada ou encerrada);
- vencida: homologada;
- sem participacao: revogada, cancelada ou ainda "agendada" com a data da
  disputa ja passada;
- aguardando disputa: agendada com data futura (ou sem data);
- valor homologado: soma do valor homologado das vencidas;
- valor empenhado: soma dos empenhos recebidos no periodo.
"""
import math
import os
from datetime import date, datetime

from flask import Blueprint, render_template, request, abort
from flask_login import login_required, current_user

from app.models import Licitacao, Empenho, Cliente

gestao_bp = Blueprint("gestao", __name__, url_prefix="/gestao")

PARTICIPOU = {"em disputa", "em julgamento", "em habilitacao", "homologada", "encerrada"}
NAO_PARTICIPOU = {"revogada", "cancelada"}

MESES = ["", "Jan", "Fev", "Mar", "Abr", "Mai", "Jun",
         "Jul", "Ago", "Set", "Out", "Nov", "Dez"]

# Fatias do grafico (disjuntas: somam o total disponibilizado)
FATIAS = [
    ("vencidas", "Vencidas", "#1f4d36"),
    ("participou_sem_vencer", "Participadas sem vitória", "#6fa585"),
    ("sem_participacao", "Sem participação", "#c9b98f"),
    ("aguardando", "Aguardando disputa", "#9fb2c6"),
]


def pode_ver_gestao(user):
    """Versao de teste: so os usuarios listados em GESTAO_USUARIOS (padrao: Igor)."""
    if not user.is_authenticated or not user.is_master():
        return False
    permitidos = {u.strip().lower() for u in os.environ.get("GESTAO_USUARIOS", "igor").split(",") if u.strip()}
    return (user.username or "").lower() in permitidos


def _mes(valor):
    """'2026-10' -> date(2026, 10, 1)."""
    try:
        ano, mes = str(valor).split("-")[:2]
        return date(int(ano), int(mes), 1)
    except (ValueError, AttributeError):
        return None


def _mais_meses(d, n):
    total = d.year * 12 + (d.month - 1) + n
    return date(total // 12, total % 12 + 1, 1)


def _ref(lic):
    dt = lic.data_disputa or lic.criado_em
    return date(dt.year, dt.month, 1) if dt else None


def _rotulo_mes(d):
    return f"{MESES[d.month]}/{d.year}"


def _metricas(lics, emps, agora):
    m = {"disponibilizadas": 0, "participadas": 0, "vencidas": 0, "sem_participacao": 0,
         "aguardando": 0, "valor_homologado": 0.0, "valor_empenhado": 0.0}
    for lic in lics:
        m["disponibilizadas"] += 1
        st = (lic.status or "").lower()
        if st in PARTICIPOU:
            m["participadas"] += 1
            if st == "homologada":
                m["vencidas"] += 1
                m["valor_homologado"] += float(lic.valor_homologado or 0)
        elif st in NAO_PARTICIPOU:
            m["sem_participacao"] += 1
        elif lic.data_disputa and lic.data_disputa < agora:
            m["sem_participacao"] += 1
        else:
            m["aguardando"] += 1
    m["valor_empenhado"] = sum(float(e.valor_total or 0) for e in emps)
    m["participou_sem_vencer"] = m["participadas"] - m["vencidas"]
    m["aproveitamento"] = round(100 * m["vencidas"] / m["participadas"]) if m["participadas"] else None
    return m


def _pizza(m):
    """Fatias do grafico como caminhos SVG (centro 100,100, raio 90)."""
    total = sum(m[ch] for ch, _, _ in FATIAS)
    fatias = []
    if not total:
        return fatias, total
    angulo = -math.pi / 2
    for chave, rotulo, cor in FATIAS:
        valor = m[chave]
        pct = valor / total
        fatia = {"chave": chave, "rotulo": rotulo, "cor": cor, "valor": valor,
                 "pct": round(pct * 100), "caminho": None, "cheio": False}
        if valor == total:
            fatia["cheio"] = True
        elif valor:
            fim = angulo + pct * 2 * math.pi
            x1, y1 = 100 + 90 * math.cos(angulo), 100 + 90 * math.sin(angulo)
            x2, y2 = 100 + 90 * math.cos(fim), 100 + 90 * math.sin(fim)
            grande = 1 if pct > 0.5 else 0
            fatia["caminho"] = f"M100,100 L{x1:.2f},{y1:.2f} A90,90 0 {grande} 1 {x2:.2f},{y2:.2f} Z"
            angulo = fim
        fatias.append(fatia)
    return fatias, total


@gestao_bp.route("/")
@login_required
def painel():
    if not pode_ver_gestao(current_user):
        abort(404)

    agora = datetime.now()
    hoje = date(agora.year, agora.month, 1)
    clientes = Cliente.query.order_by(Cliente.nome).all()
    todas = Licitacao.query.all()
    empenhos = Empenho.query.all()

    datas = [_ref(l) for l in todas if _ref(l)] + \
            [date(e.criado_em.year, e.criado_em.month, 1) for e in empenhos if e.criado_em]
    inicio_trabalhos = min(datas) if datas else hoje

    # Periodo
    periodo = request.args.get("periodo", "mes")
    de, ate = _mes(request.args.get("de")), _mes(request.args.get("ate"))
    if periodo == "personalizado" and de and ate:
        if de > ate:
            de, ate = ate, de
    elif periodo == "3m":
        de, ate = _mais_meses(hoje, -2), hoje
    elif periodo == "6m":
        de, ate = _mais_meses(hoje, -5), hoje
    elif periodo == "12m":
        de, ate = _mais_meses(hoje, -11), hoje
    elif periodo == "inicio":
        de, ate = inicio_trabalhos, max(hoje, max(datas) if datas else hoje)
    else:
        periodo = "mes"
        de, ate = hoje, hoje

    cliente_filtro = request.args.get("cliente_id", type=int)

    def no_periodo(d):
        return d is not None and de <= d <= ate

    def lics_de(cid, periodo_todo=False):
        return [l for l in todas if l.cliente_id == cid and (periodo_todo or no_periodo(_ref(l)))]

    def emps_de(cid, periodo_todo=False):
        return [e for e in empenhos if e.cliente_id == cid and e.criado_em and
                (periodo_todo or no_periodo(date(e.criado_em.year, e.criado_em.month, 1)))]

    # Tabela por cliente: periodo escolhido + desde o inicio
    linhas = []
    for c in clientes:
        m_per = _metricas(lics_de(c.id), emps_de(c.id), agora)
        m_tot = _metricas(lics_de(c.id, True), emps_de(c.id, True), agora)
        if not m_tot["disponibilizadas"] and not m_tot["valor_empenhado"]:
            continue
        linhas.append({"cliente": c, "periodo": m_per, "total": m_tot})

    # Cards e grafico: cliente filtrado ou todos
    if cliente_filtro:
        alvo_lics = lics_de(cliente_filtro)
        alvo_emps = emps_de(cliente_filtro)
    else:
        alvo_lics = [l for l in todas if no_periodo(_ref(l))]
        alvo_emps = [e for e in empenhos if e.criado_em and no_periodo(date(e.criado_em.year, e.criado_em.month, 1))]
    resumo = _metricas(alvo_lics, alvo_emps, agora)
    fatias, total_pizza = _pizza(resumo)

    # Evolucao mensal dentro do periodo (ate 24 meses)
    evolucao = []
    d = de
    while d <= ate and len(evolucao) < 24:
        lm = [l for l in alvo_lics if _ref(l) == d]
        em = [e for e in alvo_emps if date(e.criado_em.year, e.criado_em.month, 1) == d]
        mm = _metricas(lm, em, agora)
        mm["rotulo"] = _rotulo_mes(d)
        evolucao.append(mm)
        d = _mais_meses(d, 1)
    maior = max([e["disponibilizadas"] for e in evolucao] + [1])

    rotulo_periodo = _rotulo_mes(de) if de == ate else f"{_rotulo_mes(de)} a {_rotulo_mes(ate)}"
    return render_template(
        "gestao.html",
        resumo=resumo, fatias=fatias, total_pizza=total_pizza, linhas=linhas,
        clientes=clientes, cliente_filtro=cliente_filtro,
        cliente_nome=next((c.nome for c in clientes if c.id == cliente_filtro), None),
        periodo=periodo, de=de.strftime("%Y-%m"), ate=ate.strftime("%Y-%m"),
        rotulo_periodo=rotulo_periodo, evolucao=evolucao, maior=maior,
        inicio_trabalhos=_rotulo_mes(inicio_trabalhos),
    )
