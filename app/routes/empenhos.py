"""Rotas do modulo de Empenhos/Contratos."""

import os
import uuid
from flask import Blueprint, render_template, redirect, url_for, request, flash, abort
from flask_login import login_required, current_user
from datetime import datetime
from app.models import (
    Empenho, Licitacao, Cliente, DocumentoEmpenho, ObservacaoEmpenho,
    STATUS_EMPENHO, TIPO_EMPENHO,
)
from app import db

emp_bp = Blueprint("emp", __name__, url_prefix="/empenhos")


def _pode_ver_empenho(empenho):
    return current_user.pode_ver_cliente(empenho.cliente_id)


def _salvar_arquivo_empenho(f, empenho_id):
    pasta = os.path.join("/app/uploads", "empenhos", str(empenho_id))
    os.makedirs(pasta, exist_ok=True)
    nome_seguro = f"{uuid.uuid4().hex[:8]}_{f.filename}"
    caminho = os.path.join(pasta, nome_seguro)
    f.save(caminho)
    return caminho


# ─── Painel de empenhos ──────────────────────────────────────────────────────

@emp_bp.route("/")
@login_required
def painel():
    status_filtro = request.args.get("status", "todos")
    cliente_filtro = request.args.get("cliente_id", "todos")

    q = Empenho.query
    if not current_user.is_assessor():
        q = q.filter_by(cliente_id=current_user.cliente_id)
    elif current_user.is_assessor_puro():
        ids = [c.id for c in current_user.clientes_atendidos]
        q = q.filter(Empenho.cliente_id.in_(ids))

    if cliente_filtro and cliente_filtro != "todos":
        q = q.filter_by(cliente_id=int(cliente_filtro))
    if status_filtro and status_filtro != "todos":
        q = q.filter_by(status=status_filtro)

    empenhos = q.order_by(Empenho.criado_em.desc()).all()
    clientes = Cliente.query.order_by(Cliente.nome).all() if current_user.is_assessor() else []

    return render_template(
        "empenhos_painel.html",
        empenhos=empenhos,
        status_choices=STATUS_EMPENHO,
        status_filtro=status_filtro,
        cliente_filtro=cliente_filtro,
        clientes=clientes,
    )


# ─── Novo empenho ────────────────────────────────────────────────────────────

@emp_bp.route("/novo", methods=["GET", "POST"])
@login_required
def novo():
    if not current_user.is_assessor():
        abort(403)
    # Licitacoes homologadas
    licitacoes = Licitacao.query.filter_by(status="homologada").order_by(Licitacao.orgao_licitante).all()
    clientes = Cliente.query.order_by(Cliente.nome).all()

    if request.method == "POST":
        lic_id = request.form.get("licitacao_id")
        cliente_id = request.form.get("cliente_id")
        numero = request.form.get("numero_empenho", "").strip()
        contrato = request.form.get("contrato", "").strip()
        tipo = request.form.get("tipo", "ordinario")
        valor_str = request.form.get("valor_total", "").strip().replace(",", ".")
        prazo_str = request.form.get("prazo_entrega", "")

        valor = None
        if valor_str:
            try:
                valor = float(valor_str)
            except ValueError:
                pass

        prazo = None
        if prazo_str:
            try:
                prazo = datetime.strptime(prazo_str, "%Y-%m-%d").date()
            except ValueError:
                pass

        emp = Empenho(
            licitacao_id=int(lic_id),
            cliente_id=int(cliente_id),
            numero_empenho=numero,
            contrato=contrato or None,
            tipo=tipo,
            valor_total=valor,
            prazo_entrega=prazo,
            status="recebido",
        )
        db.session.add(emp)
        db.session.commit()

        # Upload de documentos
        arquivos = request.files.getlist("documentos")
        for f in arquivos:
            if f and f.filename:
                caminho = _salvar_arquivo_empenho(f, emp.id)
                doc = DocumentoEmpenho(
                    empenho_id=emp.id,
                    nome_original=f.filename,
                    caminho=caminho,
                    tamanho=f.content_length or 0,
                    enviado_por=current_user.id,
                )
                db.session.add(doc)
        db.session.commit()

        # Notifica por email
        from app.email_service import notificar_novo_empenho
        try:
            notificar_novo_empenho(emp)
        except Exception:
            pass
        flash("Empenho cadastrado.", "ok")
        return redirect(url_for("emp.detalhe", id=emp.id))

    return render_template("form_empenho.html",
                           licitacoes=licitacoes, clientes=clientes,
                           tipo_choices=TIPO_EMPENHO, emp=None)


# ─── Detalhe do empenho ──────────────────────────────────────────────────────

@emp_bp.route("/<int:id>")
@login_required
def detalhe(id):
    emp = Empenho.query.get_or_404(id)
    if not _pode_ver_empenho(emp):
        abort(403)
    return render_template("detalhe_empenho.html", emp=emp,
                           status_choices=STATUS_EMPENHO,
                           today=datetime.now().date())


# ─── Confirmar ciencia (cliente) ─────────────────────────────────────────────

@emp_bp.route("/<int:id>/confirmar-ciencia", methods=["POST"])
@login_required
def confirmar_ciencia(id):
    emp = Empenho.query.get_or_404(id)
    if not _pode_ver_empenho(emp):
        abort(403)
    if emp.status != "recebido":
        flash("Este empenho ja foi confirmado.", "erro")
        return redirect(url_for("emp.detalhe", id=id))
    emp.status = "em atendimento"
    emp.data_ciencia = datetime.utcnow()
    db.session.commit()
    flash("Ciencia confirmada. Status atualizado para 'Em Atendimento'.", "ok")
    return redirect(url_for("emp.detalhe", id=id))


# ─── Informar despacho (cliente) ─────────────────────────────────────────────

@emp_bp.route("/<int:id>/informar-despacho", methods=["POST"])
@login_required
def informar_despacho(id):
    emp = Empenho.query.get_or_404(id)
    if not _pode_ver_empenho(emp):
        abort(403)
    if emp.status != "em atendimento":
        flash("O empenho precisa estar 'Em Atendimento' para informar despacho.", "erro")
        return redirect(url_for("emp.detalhe", id=id))
    emp.status = "aguardando pagamento"
    emp.data_despacho = datetime.utcnow()
    db.session.commit()
    flash("Despacho informado. Status atualizado para 'Aguardando Pagamento'.", "ok")
    return redirect(url_for("emp.detalhe", id=id))


# ─── Marcar como pago (assessor) ────────────────────────────────────────────

@emp_bp.route("/<int:id>/marcar-pago", methods=["POST"])
@login_required
def marcar_pago(id):
    if not current_user.is_assessor():
        abort(403)
    emp = Empenho.query.get_or_404(id)
    if emp.status != "aguardando pagamento":
        flash("O empenho precisa estar 'Aguardando Pagamento'.", "erro")
        return redirect(url_for("emp.detalhe", id=id))
    emp.status = "pago"
    emp.data_pagamento = datetime.utcnow()
    db.session.commit()
    flash("Empenho marcado como pago.", "ok")
    return redirect(url_for("emp.detalhe", id=id))


# ─── Observacoes ─────────────────────────────────────────────────────────────

@emp_bp.route("/<int:id>/observacao", methods=["POST"])
@login_required
def nova_observacao(id):
    emp = Empenho.query.get_or_404(id)
    if not _pode_ver_empenho(emp):
        abort(403)
    texto = request.form.get("texto", "").strip()
    if not texto:
        flash("Escreva algo antes de enviar.", "erro")
        return redirect(url_for("emp.detalhe", id=id))
    obs = ObservacaoEmpenho(
        empenho_id=id,
        autor_id=current_user.id,
        texto=texto,
    )
    db.session.add(obs)
    db.session.commit()
    flash("Observacao registrada.", "ok")
    return redirect(url_for("emp.detalhe", id=id))


# ─── Upload de documentos ───────────────────────────────────────────────────

@emp_bp.route("/<int:id>/upload", methods=["POST"])
@login_required
def upload_documento(id):
    emp = Empenho.query.get_or_404(id)
    if not _pode_ver_empenho(emp):
        abort(403)
    arquivos = request.files.getlist("documentos")
    count = 0
    for f in arquivos:
        if f and f.filename:
            caminho = _salvar_arquivo_empenho(f, emp.id)
            doc = DocumentoEmpenho(
                empenho_id=emp.id,
                nome_original=f.filename,
                caminho=caminho,
                tamanho=f.content_length or 0,
                enviado_por=current_user.id,
            )
            db.session.add(doc)
            count += 1
    db.session.commit()
    flash(f"{count} documento(s) enviado(s).", "ok")
    return redirect(url_for("emp.detalhe", id=id))


@emp_bp.route("/documento/<int:doc_id>/excluir", methods=["POST"])
@login_required
def excluir_documento(doc_id):
    if not current_user.is_assessor():
        abort(403)
    doc = DocumentoEmpenho.query.get_or_404(doc_id)
    emp_id = doc.empenho_id
    try:
        os.remove(doc.caminho)
    except OSError:
        pass
    db.session.delete(doc)
    db.session.commit()
    flash("Documento removido.", "ok")
    return redirect(url_for("emp.detalhe", id=emp_id))
