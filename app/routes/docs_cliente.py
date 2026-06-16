from flask import Blueprint, render_template, redirect, url_for, request, flash, send_from_directory, abort
from flask_login import login_required, current_user
from app.models import Cliente, DocumentoCliente, DOCUMENTOS_CLIENTE, DOCUMENTO_TIPOS
from app import db
from datetime import datetime, date
import os, uuid

docs_bp = Blueprint("docs", __name__, url_prefix="/clientes")

UPLOAD_FOLDER = "/app/uploads/clientes"
AVISO_DIAS = 15


def _status_validade(validade):
    if not validade:
        return "sem-validade"
    hoje = date.today()
    diff = (validade - hoje).days
    if diff < 0:
        return "vencido"
    if diff <= AVISO_DIAS:
        return "vencendo"
    return "ok"


@docs_bp.route("/<int:cliente_id>/documentos")
@login_required
def documentos(cliente_id):
    cliente = Cliente.query.get_or_404(cliente_id)
    if not current_user.is_assessor() and current_user.cliente_id != cliente_id:
        abort(403)

    todos_docs = DocumentoCliente.query.filter_by(cliente_id=cliente_id).order_by(DocumentoCliente.criado_em.desc()).all()

    # Agrupa por tipo — pega o mais recente de cada tipo (exceto alteracao que pode ter multiplos)
    docs_por_tipo = {}
    for doc in todos_docs:
        if doc.tipo == "alteracao_contratual":
            docs_por_tipo.setdefault(doc.tipo, []).append(doc)
        else:
            if doc.tipo not in docs_por_tipo:
                docs_por_tipo[doc.tipo] = doc

    # Calcula status geral (pendencias)
    pendencias = []
    for slug, label in DOCUMENTOS_CLIENTE:
        doc = docs_por_tipo.get(slug)
        nao_aplica = False
        if isinstance(doc, list):
            nao_aplica = any(d.nao_se_aplica for d in doc)
            tem_doc = len(doc) > 0
        elif doc:
            nao_aplica = doc.nao_se_aplica
            tem_doc = True
        else:
            tem_doc = False
        if not tem_doc and not nao_aplica:
            pendencias.append(label)

    return render_template(
        "documentos_cliente.html",
        cliente=cliente,
        documentos_tipos=DOCUMENTOS_CLIENTE,
        docs_por_tipo=docs_por_tipo,
        pendencias=pendencias,
        status_validade=_status_validade,
        hoje=date.today(),
        aviso_dias=AVISO_DIAS,
    )


@docs_bp.route("/<int:cliente_id>/documentos/upload", methods=["POST"])
@login_required
def upload_doc(cliente_id):
    if not current_user.is_assessor():
        abort(403)
    cliente = Cliente.query.get_or_404(cliente_id)

    tipo = request.form.get("tipo", "")
    nao_aplica = request.form.get("nao_se_aplica") == "on"
    obs = request.form.get("obs", "").strip()
    validade_str = request.form.get("validade", "").strip()
    validade = None
    if validade_str:
        try:
            validade = datetime.strptime(validade_str, "%Y-%m-%d").date()
        except ValueError:
            pass

    if tipo not in DOCUMENTO_TIPOS:
        flash("Tipo de documento inválido.", "erro")
        return redirect(url_for("docs.documentos", cliente_id=cliente_id))

    arquivo = request.files.get("arquivo")

    if nao_aplica:
        # Remove docs anteriores do mesmo tipo e registra nao_se_aplica
        if tipo != "alteracao_contratual":
            DocumentoCliente.query.filter_by(cliente_id=cliente_id, tipo=tipo).delete()
        doc = DocumentoCliente(
            cliente_id=cliente_id,
            tipo=tipo,
            nome_original="N/A",
            caminho="",
            nao_se_aplica=True,
            obs=obs,
            validade=validade,
            enviado_por=current_user.id,
        )
        db.session.add(doc)
        db.session.commit()
        flash("Marcado como não se aplica.", "ok")
        return redirect(url_for("docs.documentos", cliente_id=cliente_id))

    if not arquivo or not arquivo.filename:
        flash("Selecione um arquivo.", "erro")
        return redirect(url_for("docs.documentos", cliente_id=cliente_id))

    pasta = os.path.join(UPLOAD_FOLDER, str(cliente_id))
    os.makedirs(pasta, exist_ok=True)
    ext = os.path.splitext(arquivo.filename)[1]
    nome_salvo = f"{uuid.uuid4().hex}{ext}"
    caminho = os.path.join(pasta, nome_salvo)
    arquivo.save(caminho)

    # Para tipos únicos, remove o anterior
    if tipo != "alteracao_contratual":
        antigos = DocumentoCliente.query.filter_by(cliente_id=cliente_id, tipo=tipo).all()
        for a in antigos:
            if a.caminho and os.path.exists(a.caminho):
                try:
                    os.remove(a.caminho)
                except Exception:
                    pass
            db.session.delete(a)

    doc = DocumentoCliente(
        cliente_id=cliente_id,
        tipo=tipo,
        nome_original=arquivo.filename,
        caminho=caminho,
        tamanho=os.path.getsize(caminho),
        nao_se_aplica=False,
        obs=obs,
        validade=validade,
        enviado_por=current_user.id,
    )
    db.session.add(doc)
    db.session.commit()
    flash("Documento enviado com sucesso.", "ok")
    return redirect(url_for("docs.documentos", cliente_id=cliente_id))


@docs_bp.route("/doc-cliente/<int:doc_id>/download")
@login_required
def download(doc_id):
    doc = DocumentoCliente.query.get_or_404(doc_id)
    if not current_user.is_assessor() and current_user.cliente_id != doc.cliente_id:
        abort(403)
    if not doc.caminho or not os.path.exists(doc.caminho):
        abort(404)
    pasta = os.path.dirname(doc.caminho)
    nome = os.path.basename(doc.caminho)
    return send_from_directory(pasta, nome, as_attachment=True, download_name=doc.nome_original)


@docs_bp.route("/doc-cliente/<int:doc_id>/excluir", methods=["POST"])
@login_required
def excluir(doc_id):
    if not current_user.is_assessor():
        abort(403)
    doc = DocumentoCliente.query.get_or_404(doc_id)
    cliente_id = doc.cliente_id
    if doc.caminho and os.path.exists(doc.caminho):
        try:
            os.remove(doc.caminho)
        except Exception:
            pass
    db.session.delete(doc)
    db.session.commit()
    flash("Documento removido.", "ok")
    return redirect(url_for("docs.documentos", cliente_id=cliente_id))
