"""
Tela de prospeccao de licitacoes homologadas (PNCP): lista, filtros, liga/desliga
por cliente, busca manual em segundo plano e exportacao para Excel.
"""
import io
import json
import os
import threading
from datetime import datetime, date

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


def _consulta():
    ids = [c.id for c in _clientes()]
    q = OportunidadeHomologada.query.filter(OportunidadeHomologada.cliente_id.in_(ids or [-1]))
    cid = request.args.get("cliente_id", type=int)
    if cid:
        q = q.filter_by(cliente_id=cid)
    uf = (request.args.get("uf") or "").upper()
    if uf:
        q = q.filter_by(uf=uf)
    sit = request.args.get("situacao") or "abertas"
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


@pros_bp.route("/")
@login_required
def painel():
    if not current_user.is_assessor():
        abort(403)
    clientes = _clientes()
    pagina = max(request.args.get("pagina", 1, type=int), 1)
    q = _consulta()
    total = q.count()
    ops = q.offset((pagina - 1) * 100).limit(100).all()
    ufs = sorted({u for (u,) in db.session.query(OportunidadeHomologada.uf).distinct() if u})
    acompanhando = CompraMonitorada.query.filter_by(concluida=False).count()
    return render_template("prospeccao.html", clientes=clientes, ops=ops, total=total, pagina=pagina,
                           paginas=(total + 99) // 100, ufs=ufs, situacoes=SITUACOES,
                           status=_status(), rodando=_trava.locked(), acompanhando=acompanhando,
                           filtros=request.args, desde=__import__("app.prospeccao", fromlist=["DESDE"]).DESDE)


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
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Homologadas"
    ws.append(["Cliente", "Resultado em", "Órgão", "UF", "Município", "Pregão", "Item", "Descrição",
               "Unid.", "Qtd", "Valor unit. estimado", "Vencedor", "CNPJ vencedor", "Telefone", "E-mail",
               "Cidade vencedor", "Qtd homologada", "Valor unit. homologado", "Valor total", "SRP",
               "Ata vigente até", "Situação", "Observação", "Link PNCP"])
    for op in _consulta().limit(5000):
        ws.append([op.cliente.nome if op.cliente else "", op.data_resultado, op.orgao, op.uf, op.municipio,
                   op.numero_compra, op.numero_item, op.descricao_item, op.unidade,
                   float(op.quantidade) if op.quantidade is not None else None,
                   float(op.valor_unitario_estimado) if op.valor_unitario_estimado is not None else None,
                   op.fornecedor_nome, op.fornecedor_cnpj, op.fornecedor_telefone, op.fornecedor_email,
                   f"{op.fornecedor_municipio or ''}/{op.fornecedor_uf or ''}".strip("/"),
                   float(op.quantidade_homologada) if op.quantidade_homologada is not None else None,
                   float(op.valor_unitario_homologado) if op.valor_unitario_homologado is not None else None,
                   op.valor_total_homologado, "Sim" if op.srp else ("Não" if op.srp is False else ""),
                   op.ata_vigencia_fim, op.situacao, op.observacao, op.link_pncp])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(buf, as_attachment=True,
                     download_name=f"homologadas-{date.today():%Y-%m-%d}.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
