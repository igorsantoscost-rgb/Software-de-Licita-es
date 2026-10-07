"""Lixeiras e backup (telas visiveis so para o Igor)."""
import io
import os
import zipfile
from datetime import datetime

from flask import Blueprint, render_template, redirect, url_for, request, flash, abort, send_file, Response, stream_with_context
from flask_login import login_required, current_user

from app import db
from app.models import ItemLixeira
from app.routes.gestao import pode_ver_gestao

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


def _so_igor():
    if not pode_ver_gestao(current_user):
        abort(404)


# ─── Lixeira ─────────────────────────────────────────────────────────────────

@admin_bp.route("/lixeira")
@admin_bp.route("/lixeira/<qual>")
@login_required
def lixeira(qual="licitacoes"):
    _so_igor()
    from app import lixeira as lx
    if qual not in ("licitacoes", "uploads"):
        abort(404)
    lx.limpar_vencidos()
    itens = ItemLixeira.query.filter_by(lixeira=qual).order_by(ItemLixeira.excluido_em.desc()).all()
    contagem = {q: ItemLixeira.query.filter_by(lixeira=q).count() for q in ("licitacoes", "uploads")}
    for it in itens:
        it.dias = lx.dias_restantes(it)
        it.rotulo_tipo = lx.ROTULOS_TIPO.get(it.tipo, it.tipo)
        it.tem_arquivo = any(os.path.exists(g) for _o, g in (it.arquivos or []))
        it.qtd_arquivos = len(it.arquivos or [])
    return render_template("lixeira.html", itens=itens, qual=qual, contagem=contagem,
                           dias=lx.DIAS_NA_LIXEIRA)


@admin_bp.route("/lixeira/item/<int:id>/restaurar", methods=["POST"])
@login_required
def restaurar(id):
    _so_igor()
    from app import lixeira as lx
    item = ItemLixeira.query.get_or_404(id)
    qual = item.lixeira
    ok, msg = lx.restaurar(item)
    flash(msg, "ok" if ok else "erro")
    return redirect(url_for("admin.lixeira", qual=qual))


@admin_bp.route("/lixeira/item/<int:id>/apagar", methods=["POST"])
@login_required
def apagar(id):
    _so_igor()
    from app import lixeira as lx
    item = ItemLixeira.query.get_or_404(id)
    qual, titulo = item.lixeira, item.titulo
    lx.apagar_definitivo(item)
    flash(f"\"{titulo}\" apagado definitivamente.", "ok")
    return redirect(url_for("admin.lixeira", qual=qual))


@admin_bp.route("/lixeira/item/<int:id>/baixar")
@login_required
def baixar(id):
    _so_igor()
    item = ItemLixeira.query.get_or_404(id)
    arquivos = [(o, g) for o, g in (item.arquivos or []) if os.path.exists(g)]
    if not arquivos:
        flash("Este item não tem arquivo guardado.", "erro")
        return redirect(url_for("admin.lixeira", qual=item.lixeira))
    nomes = {}
    for _tabela, dados in item.dados or []:
        if dados.get("caminho") and dados.get("nome_original"):
            nomes[dados["caminho"]] = dados["nome_original"]
        if dados.get("caminho_zip") and dados.get("nome_arquivo"):
            nomes[dados["caminho_zip"]] = dados["nome_arquivo"]
    if len(arquivos) == 1:
        o, g = arquivos[0]
        return send_file(g, as_attachment=True, download_name=nomes.get(o, os.path.basename(o)))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        usados = set()
        for o, g in arquivos:
            nome = nomes.get(o, os.path.basename(o))
            base, ext = os.path.splitext(nome)
            n = 2
            while nome in usados:
                nome = f"{base} ({n}){ext}"
                n += 1
            usados.add(nome)
            zf.write(g, nome)
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name=f"{item.titulo[:60]}.zip", mimetype="application/zip")


# ─── Backup ──────────────────────────────────────────────────────────────────

@admin_bp.route("/backup")
@login_required
def backup():
    _so_igor()
    from app import backup as bk
    return render_template("backup.html", diario=bk.info_backup_diario())


@admin_bp.route("/backup/diario")
@login_required
def baixar_diario():
    _so_igor()
    from app import backup as bk
    if not bk.info_backup_diario():
        bk.gerar_backup_diario()
    info = bk.info_backup_diario()
    if not info:
        flash("O backup diário está sendo gerado agora. Tente de novo em alguns segundos.", "erro")
        return redirect(url_for("admin.backup"))
    return send_file(bk.ARQUIVO_DIARIO, as_attachment=True,
                     download_name=f"bidfy-backup-diario-{info['gerado_em'].strftime('%Y-%m-%d')}.zip")


@admin_bp.route("/backup/dados")
@login_required
def baixar_dados_agora():
    """Todos os dados do banco neste momento (sem os arquivos)."""
    _so_igor()
    from app import backup as bk
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        bk.escrever_dados(zf)
    buf.seek(0)
    return send_file(buf, as_attachment=True, mimetype="application/zip",
                     download_name=f"bidfy-dados-{datetime.now().strftime('%Y-%m-%d-%H%M')}.zip")


@admin_bp.route("/backup/completo")
@login_required
def baixar_completo():
    """Dados + todos os arquivos enviados, num .zip montado durante o download."""
    _so_igor()
    from app import backup as bk
    nome = f"bidfy-completo-{datetime.now().strftime('%Y-%m-%d-%H%M')}.zip"
    return Response(stream_with_context(bk.gerar_backup_completo()), mimetype="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{nome}"',
                             "X-Accel-Buffering": "no"})


@admin_bp.route("/backup/gerar-diario", methods=["POST"])
@login_required
def gerar_diario_agora():
    _so_igor()
    from app import backup as bk
    if bk.gerar_backup_diario():
        flash("Backup diário atualizado agora.", "ok")
    else:
        flash("O backup já está sendo gerado. Aguarde alguns segundos.", "erro")
    return redirect(url_for("admin.backup"))
