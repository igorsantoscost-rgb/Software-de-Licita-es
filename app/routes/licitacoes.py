from flask import (Blueprint, render_template, redirect, url_for,
                   request, flash, send_from_directory, abort, jsonify)
from flask_login import login_required, current_user
from app.models import Licitacao, Documento, ItemLicitacao, Cliente, STATUS_CHOICES, PORTAL_CHOICES
from app import db
from datetime import datetime
import os, uuid, requests

lic_bp = Blueprint("lic", __name__, url_prefix="/licitacoes")

UPLOAD_FOLDER = "/app/uploads"


def _pode_ver(licitacao):
    if current_user.is_assessor():
        return True
    return licitacao.cliente_id == current_user.cliente_id


# ─── Lista / painel (redireciona para main) ──────────────────────────────────

@lic_bp.route("/nova", methods=["GET", "POST"])
@login_required
def nova():
    if not current_user.is_assessor():
        abort(403)
    clientes = Cliente.query.order_by(Cliente.nome).all()
    if request.method == "POST":
        data_str = request.form.get("data_disputa", "")
        data_disputa = None
        if data_str:
            try:
                data_disputa = datetime.strptime(data_str, "%Y-%m-%dT%H:%M")
            except ValueError:
                pass
        lic = Licitacao(
            cliente_id=int(request.form["cliente_id"]),
            orgao_licitante=request.form["orgao_licitante"].strip(),
            numero_pregao=request.form["numero_pregao"].strip(),
            uasg=request.form.get("uasg", "").strip(),
            portal=request.form.get("portal", "").strip(),
            data_disputa=data_disputa,
            status="agendada",
            objeto=request.form.get("objeto", "").strip(),
            link_edital=request.form.get("link_edital", "").strip(),
        )
        db.session.add(lic)
        db.session.commit()
        flash("Licitacao criada.", "ok")
        return redirect(url_for("lic.detalhe", id=lic.id))
    return render_template("form_licitacao.html", clientes=clientes,
                           status_choices=STATUS_CHOICES, portal_choices=PORTAL_CHOICES, lic=None)


@lic_bp.route("/<int:id>")
@login_required
def detalhe(id):
    lic = Licitacao.query.get_or_404(id)
    if not _pode_ver(lic):
        abort(403)
    return render_template("detalhe_licitacao.html", lic=lic,
                           status_choices=STATUS_CHOICES, portal_choices=PORTAL_CHOICES)


@lic_bp.route("/<int:id>/editar", methods=["GET", "POST"])
@login_required
def editar(id):
    if not current_user.is_assessor():
        abort(403)
    lic = Licitacao.query.get_or_404(id)
    clientes = Cliente.query.order_by(Cliente.nome).all()
    if request.method == "POST":
        data_str = request.form.get("data_disputa", "")
        if data_str:
            try:
                lic.data_disputa = datetime.strptime(data_str, "%Y-%m-%dT%H:%M")
            except ValueError:
                pass
        lic.cliente_id = int(request.form["cliente_id"])
        lic.orgao_licitante = request.form["orgao_licitante"].strip()
        lic.numero_pregao = request.form["numero_pregao"].strip()
        lic.uasg = request.form.get("uasg", "").strip()
        lic.portal = request.form.get("portal", "").strip()
        lic.objeto = request.form.get("objeto", "").strip()
        lic.link_edital = request.form.get("link_edital", "").strip()
        db.session.commit()
        flash("Licitacao atualizada.", "ok")
        return redirect(url_for("lic.detalhe", id=lic.id))
    return render_template("form_licitacao.html", clientes=clientes,
                           status_choices=STATUS_CHOICES, portal_choices=PORTAL_CHOICES, lic=lic)


@lic_bp.route("/<int:id>/status", methods=["POST"])
@login_required
def atualizar_status(id):
    if not current_user.is_assessor():
        abort(403)
    lic = Licitacao.query.get_or_404(id)
    novo = request.form.get("status")
    if novo in STATUS_CHOICES:
        lic.status = novo
        db.session.commit()
        flash(f"Status atualizado para '{novo}'.", "ok")
    return redirect(url_for("lic.detalhe", id=lic.id))


# ─── Documentos ──────────────────────────────────────────────────────────────

@lic_bp.route("/<int:id>/upload", methods=["POST"])
@login_required
def upload(id):
    if not current_user.is_assessor():
        abort(403)
    lic = Licitacao.query.get_or_404(id)
    arquivos = request.files.getlist("arquivos")
    pasta = os.path.join(UPLOAD_FOLDER, str(id))
    os.makedirs(pasta, exist_ok=True)
    for f in arquivos:
        if not f.filename:
            continue
        ext = os.path.splitext(f.filename)[1]
        nome_salvo = f"{uuid.uuid4().hex}{ext}"
        caminho = os.path.join(pasta, nome_salvo)
        f.save(caminho)
        doc = Documento(
            licitacao_id=id,
            nome_original=f.filename,
            caminho=caminho,
            tamanho=os.path.getsize(caminho),
            enviado_por=current_user.id,
        )
        db.session.add(doc)
    db.session.commit()
    flash("Documentos enviados.", "ok")
    return redirect(url_for("lic.detalhe", id=id))


@lic_bp.route("/doc/<int:doc_id>/download")
@login_required
def download_doc(doc_id):
    doc = Documento.query.get_or_404(doc_id)
    lic = Licitacao.query.get_or_404(doc.licitacao_id)
    if not _pode_ver(lic):
        abort(403)
    pasta = os.path.dirname(doc.caminho)
    nome_arquivo = os.path.basename(doc.caminho)
    return send_from_directory(pasta, nome_arquivo, as_attachment=True,
                               download_name=doc.nome_original)


@lic_bp.route("/doc/<int:doc_id>/excluir", methods=["POST"])
@login_required
def excluir_doc(doc_id):
    if not current_user.is_assessor():
        abort(403)
    doc = Documento.query.get_or_404(doc_id)
    lic_id = doc.licitacao_id
    try:
        os.remove(doc.caminho)
    except FileNotFoundError:
        pass
    db.session.delete(doc)
    db.session.commit()
    flash("Documento removido.", "ok")
    return redirect(url_for("lic.detalhe", id=lic_id))


# ─── Itens / Lotes ────────────────────────────────────────────────────────────

@lic_bp.route("/<int:id>/itens/adicionar", methods=["POST"])
@login_required
def adicionar_item(id):
    lic = Licitacao.query.get_or_404(id)
    if not _pode_ver(lic):
        abort(403)
    descricao = request.form.get("descricao", "").strip()
    if not descricao:
        flash("Descricao do item e obrigatoria.", "erro")
        return redirect(url_for("lic.detalhe", id=id))
    valor_str = request.form.get("valor_minimo", "").replace(",", ".")
    valor = None
    try:
        valor = float(valor_str) if valor_str else None
    except ValueError:
        pass
    qtd_str = request.form.get("quantidade", "")
    qtd = None
    try:
        qtd = int(qtd_str) if qtd_str else None
    except ValueError:
        pass
    item = ItemLicitacao(
        licitacao_id=id,
        descricao=descricao,
        lote_grupo=request.form.get("lote_grupo", "").strip(),
        valor_minimo=valor,
        unidade=request.form.get("unidade", "").strip(),
        quantidade=qtd,
    )
    db.session.add(item)
    db.session.commit()
    flash("Item adicionado.", "ok")
    return redirect(url_for("lic.detalhe", id=id))


@lic_bp.route("/item/<int:item_id>/editar", methods=["POST"])
@login_required
def editar_item(item_id):
    item = ItemLicitacao.query.get_or_404(item_id)
    lic = Licitacao.query.get_or_404(item.licitacao_id)
    if not _pode_ver(lic):
        abort(403)
    item.descricao = request.form.get("descricao", item.descricao).strip()
    item.lote_grupo = request.form.get("lote_grupo", "").strip()
    item.unidade = request.form.get("unidade", "").strip()
    valor_str = request.form.get("valor_minimo", "").replace(",", ".")
    try:
        item.valor_minimo = float(valor_str) if valor_str else None
    except ValueError:
        pass
    qtd_str = request.form.get("quantidade", "")
    try:
        item.quantidade = int(qtd_str) if qtd_str else None
    except ValueError:
        pass
    db.session.commit()
    flash("Item atualizado.", "ok")
    return redirect(url_for("lic.detalhe", id=lic.id))


@lic_bp.route("/item/<int:item_id>/excluir", methods=["POST"])
@login_required
def excluir_item(item_id):
    item = ItemLicitacao.query.get_or_404(item_id)
    lic_id = item.licitacao_id
    db.session.delete(item)
    db.session.commit()
    flash("Item removido.", "ok")
    return redirect(url_for("lic.detalhe", id=lic_id))


# ─── Resumo com IA ───────────────────────────────────────────────────────────

@lic_bp.route("/<int:id>/resumo-ia", methods=["POST"])
@login_required
def gerar_resumo(id):
    if not current_user.is_assessor():
        abort(403)
    lic = Licitacao.query.get_or_404(id)

    texto_base = f"""
Orgao: {lic.orgao_licitante}
Pregao: {lic.numero_pregao}
UASG: {lic.uasg or 'nao informado'}
Portal: {lic.portal or 'nao informado'}
Data da disputa: {lic.data_disputa.strftime('%d/%m/%Y %H:%M') if lic.data_disputa else 'nao informada'}
Objeto: {lic.objeto or 'nao informado'}
Link do edital: {lic.link_edital or 'nao informado'}
Itens:
"""
    for item in lic.itens:
        texto_base += f"- {item.descricao} | Lote/Grupo: {item.lote_grupo or '-'} | Qtd: {item.quantidade or '-'} {item.unidade or ''}\n"

    prompt = f"""Voce e um especialista em licitacoes publicas brasileiras.
Com base nas informacoes abaixo, gere um resumo executivo claro e objetivo da oportunidade para o cliente.
Destaque: objeto do pregao, data e horario da disputa, prazo estimado de entrega (se possivel inferir), 
prazo de pagamento (se disponivel), se ha lotes ou grupos, pontos de atencao e proximo passo recomendado.
Escreva em portugues, de forma direta, em no maximo 250 palavras.

{texto_base}
"""

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"Content-Type": "application/json"},
            json={
                "model": "claude-sonnet-4-6",
                "max_tokens": 600,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=30,
        )
        data = resp.json()
        resumo = data["content"][0]["text"]
    except Exception as e:
        resumo = f"Erro ao gerar resumo: {str(e)}"

    lic.resumo_ia = resumo
    db.session.commit()
    flash("Resumo gerado com sucesso.", "ok")
    return redirect(url_for("lic.detalhe", id=id))
