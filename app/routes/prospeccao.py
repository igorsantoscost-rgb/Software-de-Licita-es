"""
Tela de prospeccao de licitacoes homologadas (PNCP): lista, filtros, liga/desliga
por cliente, busca manual em segundo plano e exportacao para Excel.
"""
import io
import json
import os
import threading
from datetime import datetime, date, timedelta

from flask import (Blueprint, render_template, redirect, url_for, request, flash,
                   abort, current_app, send_file)
from flask_login import login_required, current_user
from sqlalchemy import or_

from app import db
from app.models import Cliente, OportunidadeHomologada, CompraMonitorada, agora_brasilia

pros_bp = Blueprint("pros", __name__, url_prefix="/prospeccao")

ARQ_STATUS = "/app/uploads/_prospeccao_status.json"
_trava = threading.Lock()

SITUACOES = [("abertas", "Novas, vistas e contatadas"), ("nova", "Novas"), ("vista", "Vistas"),
             ("contatada", "Contatadas"), ("descartada", "Descartadas"), ("todas", "Todas")]


def _clientes():
    if current_user.is_master():
        return Cliente.query.order_by(Cliente.nome).all()
    return current_user.clientes_atendidos.order_by(Cliente.nome).all()


def _status():
    try:
        with open(ARQ_STATUS, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _gravar_status(dados):
    try:
        os.makedirs(os.path.dirname(ARQ_STATUS), exist_ok=True)
        with open(ARQ_STATUS, "w", encoding="utf-8") as f:
            json.dump(dados, f, ensure_ascii=False)
    except Exception:
        pass


def _semana(padrao_ultima=False):
    """Segunda-feira da semana escolhida em ?semana=AAAA-MM-DD (ou a ultima completa)."""
    txt = request.args.get("semana")
    if txt == "todas":
        return None
    if txt:
        try:
            d = datetime.strptime(txt, "%Y-%m-%d").date()
            return d - timedelta(days=d.weekday())
        except ValueError:
            pass
    if padrao_ultima:
        from app.prospeccao import ultima_semana
        return ultima_semana()[0]
    return None


def _consulta(cliente_fixo=None):
    """Oportunidades com os filtros da tela. cliente_fixo = visao do cliente:
    so as dele, sem descartadas, e por padrao a ultima semana completa."""
    if cliente_fixo:
        q = OportunidadeHomologada.query.filter(OportunidadeHomologada.cliente_id == cliente_fixo,
                                                OportunidadeHomologada.situacao != "descartada")
    else:
        ids = [c.id for c in _clientes()]
        q = OportunidadeHomologada.query.filter(OportunidadeHomologada.cliente_id.in_(ids or [-1]))
        cid = request.args.get("cliente_id", type=int)
        if cid:
            q = q.filter_by(cliente_id=cid)
    seg = _semana(padrao_ultima=bool(cliente_fixo))
    if seg:
        ini = datetime.combine(seg, datetime.min.time())
        q = q.filter(OportunidadeHomologada.data_resultado >= ini,
                     OportunidadeHomologada.data_resultado < ini + timedelta(days=7))
    uf = (request.args.get("uf") or "").upper()
    if uf:
        q = q.filter_by(uf=uf)
    uf_v = (request.args.get("uf_vencedor") or "").upper()
    if uf_v:
        q = q.filter_by(fornecedor_uf=uf_v)
    sit = "todas" if cliente_fixo else (request.args.get("situacao") or "abertas")
    if sit == "abertas":
        q = q.filter(OportunidadeHomologada.situacao != "descartada")
    elif sit != "todas":
        q = q.filter_by(situacao=sit)
    if request.args.get("ata") == "1":
        q = q.filter(OportunidadeHomologada.ata_vigencia_fim >= date.today())
    termo = (request.args.get("q") or "").strip()
    if termo:
        like = f"%{termo}%"
        q = q.filter(or_(OportunidadeHomologada.descricao_item.ilike(like),
                         OportunidadeHomologada.fornecedor_nome.ilike(like),
                         OportunidadeHomologada.orgao.ilike(like),
                         OportunidadeHomologada.fornecedor_cnpj.ilike(like)))
    return q.order_by(OportunidadeHomologada.data_resultado.desc().nullslast(),
                      OportunidadeHomologada.id.desc())


POR_PAGINA = 40


def _agrupar(ops):
    """Junta os itens por licitacao (cliente + pregao). Devolve lista de dicts,
    a licitacao com resultado mais recente primeiro."""
    from datetime import date as _date
    grupos = {}
    for op in ops:
        chave = (op.cliente_id, op.cnpj_orgao, op.ano, op.sequencial)
        g = grupos.get(chave)
        if g is None:
            g = grupos[chave] = {"chave": chave, "itens": [], "primeiro": op, "total": 0.0,
                                 "data": None, "vencedores": {}, "ata": None, "srp": None,
                                 "situacoes": set(), "observacao": None}
        g["itens"].append(op)
        t = op.valor_total_homologado
        if t:
            g["total"] += t
        if op.data_resultado and (g["data"] is None or op.data_resultado > g["data"]):
            g["data"] = op.data_resultado
        nome = op.fornecedor_nome or op.fornecedor_cnpj
        v = g["vencedores"].setdefault(nome, {"op": op, "itens": 0, "total": 0.0})
        v["itens"] += 1
        v["total"] += t or 0
        if op.ata_vigencia_fim and (g["ata"] is None or op.ata_vigencia_fim > g["ata"]):
            g["ata"] = op.ata_vigencia_fim
        if op.srp:
            g["srp"] = True
        g["situacoes"].add(op.situacao)
        g["observacao"] = g["observacao"] or op.observacao
    lista = list(grupos.values())
    hoje = _date.today()
    for g in lista:
        g["itens"].sort(key=lambda o: (o.numero_item, o.fornecedor_nome or ""))
        g["vencedores"] = sorted(g["vencedores"].items(), key=lambda kv: -kv[1]["total"])
        sits = g["situacoes"]
        g["situacao"] = next(iter(sits)) if len(sits) == 1 else ("nova" if "nova" in sits else "vista")
        g["ata_vigente"] = bool(g["ata"] and g["ata"] >= hoje)
    lista.sort(key=lambda g: (g["data"] is not None, g["data"] or datetime.min), reverse=True)
    return lista


@pros_bp.route("/")
@login_required
def painel():
    if not current_user.is_assessor():
        abort(403)
    clientes = _clientes()
    pagina = max(request.args.get("pagina", 1, type=int), 1)
    grupos = _agrupar(_consulta().limit(20000).all())
    total = len(grupos)
    licitacoes = grupos[(pagina - 1) * POR_PAGINA: pagina * POR_PAGINA]
    from app.capag import UFS
    ufs = [sigla for sigla, _nome in sorted(UFS)]
    acompanhando = CompraMonitorada.query.filter_by(concluida=False).count()
    sem_uf = OportunidadeHomologada.query.filter(OportunidadeHomologada.uf.is_(None)).count()
    sem_objeto = OportunidadeHomologada.query.filter(OportunidadeHomologada.objeto.is_(None)).count()
    return render_template("prospeccao.html", clientes=clientes, licitacoes=licitacoes, sem_objeto=sem_objeto, total=total,
                           total_itens=sum(len(g["itens"]) for g in grupos), pagina=pagina,
                           paginas=(total + POR_PAGINA - 1) // POR_PAGINA, ufs=ufs, situacoes=SITUACOES,
                           status=_status(), rodando=_trava.locked(), acompanhando=acompanhando, sem_uf=sem_uf,
                           filtros=request.args, desde=__import__("app.prospeccao", fromlist=["DESDE"]).DESDE,
                           **_comum(None))


def _comum(cliente_fixo):
    from app.prospeccao import semanas_disponiveis
    args = {k: v for k, v in request.args.items() if k != "pagina" and v}
    return {"semanas": semanas_disponiveis(), "semana_sel": _semana(padrao_ultima=bool(cliente_fixo)),
            "args": args, "visao_cliente": bool(cliente_fixo), "timedelta": timedelta}


def _cliente_da_tela():
    """Cliente logado; assessor pode pre-visualizar com ?ver_cliente=<id>."""
    if current_user.is_assessor():
        cid = request.args.get("ver_cliente", type=int)
        if not cid or not current_user.pode_ver_cliente(cid):
            abort(404)
        return cid
    if not current_user.cliente_id:
        abort(403)
    return current_user.cliente_id


@pros_bp.route("/leads")
@login_required
def leads():
    """LEADS INDIRETOS na visao do cliente: so as oportunidades dele."""
    cid = _cliente_da_tela()
    pagina = max(request.args.get("pagina", 1, type=int), 1)
    grupos = _agrupar(_consulta(cliente_fixo=cid).limit(20000).all())
    total = len(grupos)
    from app.capag import UFS
    return render_template("prospeccao.html", clientes=[], licitacoes=grupos[(pagina - 1) * POR_PAGINA: pagina * POR_PAGINA],
                           total=total, total_itens=sum(len(g["itens"]) for g in grupos), pagina=pagina,
                           paginas=(total + POR_PAGINA - 1) // POR_PAGINA,
                           ufs=[sigla for sigla, _n in sorted(UFS)], situacoes=SITUACOES, status={}, rodando=False,
                           acompanhando=0, sem_uf=0, sem_objeto=0, filtros=request.args, cliente=Cliente.query.get(cid),
                           desde=__import__("app.prospeccao", fromlist=["DESDE"]).DESDE, **_comum(cid))


@pros_bp.route("/leads.xlsx")
@login_required
def leads_exportar():
    cid = _cliente_da_tela()
    seg = _semana(padrao_ultima=True)
    nome = f"leads-indiretos-{seg:%Y-%m-%d}.xlsx" if seg else f"leads-indiretos-{date.today():%Y-%m-%d}.xlsx"
    return _planilha(_consulta(cliente_fixo=cid), nome, visao_cliente=True)


@pros_bp.route("/licitacao/situacao", methods=["POST"])
@login_required
def situacao_licitacao():
    """Marca a situacao (e a observacao) de todos os itens de uma licitacao."""
    if not current_user.is_assessor():
        abort(403)
    cid = request.form.get("cliente_id", type=int)
    if not cid or not current_user.pode_ver_cliente(cid):
        abort(403)
    q = OportunidadeHomologada.query.filter_by(
        cliente_id=cid, cnpj_orgao=request.form.get("cnpj", ""),
        ano=request.form.get("ano", type=int), sequencial=request.form.get("seq", type=int))
    dados = {}
    nova = request.form.get("situacao")
    if nova in ("nova", "vista", "contatada", "descartada"):
        dados["situacao"] = nova
    if "observacao" in request.form:
        dados["observacao"] = request.form.get("observacao", "").strip()[:2000] or None
    if dados:
        q.update(dados, synchronize_session=False)
        db.session.commit()
    return redirect(request.referrer or url_for("pros.painel"))


@pros_bp.route("/cliente/<int:id>/ligar", methods=["POST"])
@login_required
def ligar(id):
    if not current_user.is_assessor() or not current_user.pode_ver_cliente(id):
        abort(403)
    c = Cliente.query.get_or_404(id)
    c.prospectar_homologadas = not bool(c.prospectar_homologadas)
    db.session.commit()
    if c.prospectar_homologadas and not c.palavras_chave:
        flash(f"Prospecção ligada para {c.nome}, mas ele não tem palavras-chave cadastradas.", "erro")
    else:
        flash(f"Prospecção {'ligada' if c.prospectar_homologadas else 'desligada'} para {c.nome}.", "ok")
    return redirect(request.referrer or url_for("pros.painel"))


@pros_bp.route("/rodar", methods=["POST"])
@login_required
def rodar():
    if not current_user.is_assessor():
        abort(403)
    if _trava.locked():
        flash("A busca já está rodando. Atualize a página daqui a alguns minutos.", "ok")
        return redirect(url_for("pros.painel"))
    app = current_app._get_current_object()

    def tarefa():
        with _trava, app.app_context():
            from app.prospeccao import rodar_prospeccao
            _gravar_status({"inicio": agora_brasilia().strftime("%d/%m/%Y %H:%M"), "rodando": True})
            try:
                res = rodar_prospeccao(enviar_email=False, log=lambda m: app.logger.info(m))
                _gravar_status({"fim": agora_brasilia().strftime("%d/%m/%Y %H:%M"), **res})
            except Exception as e:
                app.logger.exception("Prospecção manual falhou")
                _gravar_status({"fim": agora_brasilia().strftime("%d/%m/%Y %H:%M"), "erro": str(e)[:200]})
            finally:
                db.session.remove()

    threading.Thread(target=tarefa, daemon=True).start()
    flash("Busca iniciada. A primeira pode levar de 10 a 30 minutos; as oportunidades aparecem aqui conforme forem achadas.", "ok")
    return redirect(url_for("pros.painel"))


@pros_bp.route("/completar-ufs", methods=["POST"])
@login_required
def completar_ufs_agora():
    if not current_user.is_assessor():
        abort(403)
    from app.prospeccao import completar_ufs, completar_compras
    n = completar_ufs(limite=150)
    _c, k = completar_compras(limite=40, log=lambda m: None)
    flash(f"UF preenchida em {n} oportunidade(s); objeto/número completados em {k} pregão(ões).", "ok")
    return redirect(request.referrer or url_for("pros.painel"))


@pros_bp.route("/<int:id>/situacao", methods=["POST"])
@login_required
def situacao(id):
    if not current_user.is_assessor():
        abort(403)
    op = OportunidadeHomologada.query.get_or_404(id)
    if not current_user.pode_ver_cliente(op.cliente_id):
        abort(403)
    nova = request.form.get("situacao")
    if nova in ("nova", "vista", "contatada", "descartada"):
        op.situacao = nova
    if "observacao" in request.form:
        op.observacao = request.form.get("observacao", "").strip()[:2000] or None
    db.session.commit()
    return redirect(request.referrer or url_for("pros.painel"))


@pros_bp.route("/exportar.xlsx")
@login_required
def exportar():
    if not current_user.is_assessor():
        abort(403)
    return _planilha(_consulta(), f"leads-indiretos-{date.today():%Y-%m-%d}.xlsx")


def _alto(v):
    return v.upper() if isinstance(v, str) else v


def _planilha(consulta, nome, visao_cliente=False):
    """Uma linha por item, tudo em caixa alta. Ordem: licitacao, item,
    valores estimado e homologado, depois os dados do vencedor."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter
    wb = Workbook()
    ws = wb.active
    ws.title = "LEADS INDIRETOS"
    colunas = [  # (titulo, largura, formato)
        ("ÓRGÃO", 40, None), ("UF", 5, None), ("MUNICÍPIO", 20, None), ("PREGÃO", 12, None),
        ("OBJETO", 55, None), ("ITEM", 8, "0"), ("DESCRIÇÃO", 60, None), ("UNID.", 10, None),
        ("QTD", 10, "#,##0.##"), ("VALOR UNIT. ESTIMADO", 16, "#,##0.00"),
        ("QTD HOMOLOGADA", 14, "#,##0.##"), ("VALOR UNIT. HOMOLOGADO", 18, "#,##0.00"),
        ("VALOR TOTAL HOMOLOGADO", 18, "#,##0.00"),
        ("VENCEDOR", 40, None), ("CNPJ VENCEDOR", 18, None), ("TELEFONE", 22, None),
        ("E-MAIL", 30, None), ("CIDADE VENCEDOR", 22, None),
    ]
    if not visao_cliente:
        colunas = [("CLIENTE", 25, None)] + colunas + [("SITUAÇÃO", 12, None), ("OBSERVAÇÃO", 40, None)]
    ws.append([c[0] for c in colunas])
    for cel in ws[1]:
        cel.font = Font(bold=True, color="FFFFFF")
        cel.fill = PatternFill("solid", fgColor="14532D")
        cel.alignment = Alignment(vertical="center", wrap_text=True)
    num = lambda v: float(v) if v is not None else None
    for op in consulta.limit(10000):
        linha = [op.orgao, op.uf, op.municipio, op.numero_compra, op.objeto, op.numero_item,
                 op.descricao_item, op.unidade, num(op.quantidade), num(op.valor_unitario_estimado),
                 num(op.quantidade_homologada), num(op.valor_unitario_homologado), op.valor_total_homologado,
                 op.fornecedor_nome, op.fornecedor_cnpj, op.fornecedor_telefone, op.fornecedor_email,
                 f"{op.fornecedor_municipio or ''}/{op.fornecedor_uf or ''}".strip("/")]
        if not visao_cliente:
            linha = [op.cliente.nome if op.cliente else ""] + linha + [op.situacao, op.observacao]
        ws.append([_alto(v) for v in linha])
    for i, (_t, larg, fmt) in enumerate(colunas, start=1):
        letra = get_column_letter(i)
        ws.column_dimensions[letra].width = larg
        if fmt:
            for (cel,) in ws.iter_rows(min_row=2, min_col=i, max_col=i):
                cel.number_format = fmt
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name=nome,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
