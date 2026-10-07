"""
Importacao em lote: o assessor sobe o ZIP do sistema de busca de editais,
confere a lista de processos encontrados e o Bidfy cria uma licitacao por
pasta (com os arquivos, os itens e os campos basicos ja preenchidos).
"""
import json
import os
import shutil
import time
import uuid
from datetime import datetime

from flask import (Blueprint, render_template, redirect, url_for, request,
                   flash, abort, current_app, send_file)
from flask_login import login_required, current_user

from app import db
from app.models import (Licitacao, Documento, ItemLicitacao, Cliente,
                        CapagMunicipio, PacoteImportado)
from app.capag import consultar as consultar_capag, normalizar
from app.importador_pacote import extrair_zip, analisar_pacote, BaseMunicipios
from app.classificador_cliente import Classificador

imp_bp = Blueprint("imp", __name__, url_prefix="/licitacoes/importar")

UPLOAD_FOLDER = "/app/uploads"
PASTA_IMPORTACOES = os.path.join(UPLOAD_FOLDER, "_importacoes")
PASTA_PACOTES = os.path.join(UPLOAD_FOLDER, "_pacotes")  # zips originais (historico)
HORAS_PARA_LIMPAR = 48


def _clientes_do_usuario():
    if current_user.is_master():
        return Cliente.query.order_by(Cliente.nome).all()
    return current_user.clientes_atendidos.order_by(Cliente.nome).all()


def _pasta_token(token):
    # token e sempre hex gerado aqui; qualquer outra coisa e recusada
    if not token or not all(c in "0123456789abcdef" for c in token) or len(token) != 32:
        abort(404)
    return os.path.join(PASTA_IMPORTACOES, token)


def _ler_analise(token):
    caminho = os.path.join(_pasta_token(token), "analise.json")
    if not os.path.exists(caminho):
        return None
    with open(caminho, encoding="utf-8") as f:
        return json.load(f)


def _limpar_importacoes_antigas():
    if not os.path.isdir(PASTA_IMPORTACOES):
        return
    limite = time.time() - HORAS_PARA_LIMPAR * 3600
    for nome in os.listdir(PASTA_IMPORTACOES):
        caminho = os.path.join(PASTA_IMPORTACOES, nome)
        try:
            if os.path.getmtime(caminho) < limite:
                shutil.rmtree(caminho, ignore_errors=True)
        except OSError:
            pass


def _base_municipios():
    try:
        registros = [(m.nome, m.uf, m.nome_normalizado)
                     for m in CapagMunicipio.query.with_entities(
                         CapagMunicipio.nome, CapagMunicipio.uf, CapagMunicipio.nome_normalizado)]
    except Exception:
        db.session.rollback()
        registros = []
    return BaseMunicipios(registros) if registros else None


def _classificador(clientes):
    return Classificador([(c.id, c.nome, [p.palavra for p in c.palavras_chave]) for c in clientes])


def _ja_cadastrada(cliente_id, proposta):
    """Procura a mesma licitacao ja cadastrada para o cliente."""
    if proposta.get("codigo_busca"):
        lic = Licitacao.query.filter_by(cliente_id=cliente_id,
                                        codigo_busca=proposta["codigo_busca"]).first()
        if lic:
            return lic
    if proposta.get("uasg") and proposta.get("numero_pregao"):
        lic = Licitacao.query.filter_by(cliente_id=cliente_id,
                                        uasg=proposta["uasg"],
                                        numero_pregao=proposta["numero_pregao"]).first()
        if lic:
            return lic
    return None


# ─── Passo 1: enviar o ZIP ───────────────────────────────────────────────────

@imp_bp.route("/", methods=["GET", "POST"])
@login_required
def enviar():
    if not current_user.is_assessor():
        abort(403)
    clientes = _clientes_do_usuario()

    if request.method == "POST":
        # Sem cliente escolhido = o Bidfy identifica pelos itens de cada edital
        cliente_forcado = request.form.get("cliente_id", type=int)
        arquivo = request.files.get("pacote")
        if cliente_forcado and not current_user.pode_ver_cliente(cliente_forcado):
            abort(403)
        if not arquivo or not arquivo.filename.lower().endswith(".zip"):
            flash("Envie o arquivo .zip gerado pelo sistema de busca de editais.", "erro")
            return redirect(url_for("imp.enviar"))

        # O zip original fica guardado e registrado no historico de pacotes
        os.makedirs(PASTA_PACOTES, exist_ok=True)
        caminho_zip = os.path.join(PASTA_PACOTES, f"{uuid.uuid4().hex}.zip")
        arquivo.save(caminho_zip)
        pacote = PacoteImportado(nome_arquivo=(arquivo.filename or "pacote.zip")[:300],
                                 caminho_zip=caminho_zip, tamanho=os.path.getsize(caminho_zip),
                                 enviado_por=current_user.id, situacao="em conferencia")
        db.session.add(pacote)
        db.session.commit()
        return _analisar_e_conferir(pacote, cliente_forcado, clientes)

    pacotes = PacoteImportado.query.order_by(PacoteImportado.enviado_em.desc()).limit(200).all()
    for pc in pacotes:
        pc.conferencia_aberta = bool(pc.token and _ler_analise(pc.token))
    sem_palavras = [c for c in clientes if not c.palavras_chave]
    return render_template("importar_pacote.html", clientes=clientes, sem_palavras=sem_palavras,
                           pacotes=pacotes)


def _analisar_e_conferir(pacote, cliente_forcado, clientes):
    """Extrai o zip guardado do pacote, monta as propostas e abre a conferencia."""
    _limpar_importacoes_antigas()
    token = uuid.uuid4().hex
    pasta = _pasta_token(token)
    os.makedirs(pasta, exist_ok=True)
    try:
        raiz = extrair_zip(pacote.caminho_zip, os.path.join(pasta, "extraido"))
        analise = analisar_pacote(raiz, _base_municipios())
    except Exception as e:
        shutil.rmtree(pasta, ignore_errors=True)
        pacote.situacao = "com erro"
        db.session.commit()
        flash(f"Não consegui abrir o ZIP: {e}", "erro")
        return redirect(url_for("imp.enviar"))

    if not analise["propostas"]:
        shutil.rmtree(pasta, ignore_errors=True)
        pacote.situacao = "com erro"
        db.session.commit()
        flash("O ZIP não tem nenhuma pasta de processo dentro.", "erro")
        return redirect(url_for("imp.enviar"))

    classificador = _classificador(clientes)
    for p in analise["propostas"]:
        resultado = classificador.classificar(p.get("itens"))
        p["classificacao"] = resultado
        if cliente_forcado:
            p["cliente_sugerido"] = cliente_forcado
            p["origem_cliente"] = "escolhido"
        else:
            p["cliente_sugerido"] = resultado["cliente_id"]
            p["origem_cliente"] = "itens" if resultado["cliente_id"] else None
            if not resultado["itens_total"]:
                p["avisos"].insert(0, "Sem itens na planilha: escolha o cliente.")
            elif not resultado["cliente_id"]:
                p["avisos"].insert(0, "Nenhuma palavra-chave de cliente nos itens: escolha o cliente.")
            elif resultado["empate"]:
                p["avisos"].insert(0, "Empate entre clientes: confira o cliente sugerido.")
        p["ja_cadastrada"] = None
        if p["cliente_sugerido"]:
            existente = _ja_cadastrada(p["cliente_sugerido"], p)
            if existente:
                p["ja_cadastrada"] = existente.id
                p["selecionada"] = False
                p["avisos"].insert(0, "Já cadastrada para este cliente.")

    analise["cliente_id"] = cliente_forcado
    analise["nome_pacote"] = pacote.nome_arquivo
    analise["pacote_id"] = pacote.id
    analise["criado_por"] = current_user.id
    analise["criado_em"] = datetime.now().isoformat()
    with open(os.path.join(pasta, "analise.json"), "w", encoding="utf-8") as f:
        json.dump(analise, f, ensure_ascii=False)
    pacote.token = token
    pacote.situacao = "em conferencia" if pacote.situacao != "importado" else pacote.situacao
    pacote.qtd_processos = len(analise["propostas"])
    db.session.commit()
    return redirect(url_for("imp.conferir", token=token))


def _pacote_do_token(token, analise):
    pid = (analise or {}).get("pacote_id")
    return PacoteImportado.query.get(pid) if pid else PacoteImportado.query.filter_by(token=token).first()


# ─── Historico: conferir de novo, baixar e excluir ───────────────────────────

@imp_bp.route("/pacote/<int:id>/conferir", methods=["POST"])
@login_required
def reconferir(id):
    if not current_user.is_assessor():
        abort(403)
    pacote = PacoteImportado.query.get_or_404(id)
    if pacote.token and _ler_analise(pacote.token):
        return redirect(url_for("imp.conferir", token=pacote.token))
    if not pacote.caminho_zip or not os.path.exists(pacote.caminho_zip):
        flash("O arquivo deste pacote não está mais guardado.", "erro")
        return redirect(url_for("imp.enviar"))
    return _analisar_e_conferir(pacote, None, _clientes_do_usuario())


@imp_bp.route("/pacote/<int:id>/baixar")
@login_required
def baixar_pacote(id):
    if not current_user.is_assessor():
        abort(403)
    pacote = PacoteImportado.query.get_or_404(id)
    if not pacote.caminho_zip or not os.path.exists(pacote.caminho_zip):
        flash("O arquivo deste pacote não está mais guardado.", "erro")
        return redirect(url_for("imp.enviar"))
    return send_file(pacote.caminho_zip, as_attachment=True, download_name=pacote.nome_arquivo)


@imp_bp.route("/pacote/<int:id>/excluir", methods=["POST"])
@login_required
def excluir_pacote(id):
    if not current_user.is_assessor():
        abort(403)
    from app import lixeira
    pacote = PacoteImportado.query.get_or_404(id)
    excluidas, mantidas = 0, 0
    if request.form.get("com_licitacoes") == "1":
        for lic_id in pacote.ids_licitacoes:
            lic = Licitacao.query.get(lic_id)
            if not lic:
                continue
            if lic.empenhos:
                mantidas += 1
                continue
            lixeira.enviar_licitacao(lic, usuario_id=current_user.id,
                                     motivo=f"pacote {pacote.nome_arquivo}")
            db.session.delete(lic)
            excluidas += 1
    if pacote.token:
        shutil.rmtree(_pasta_token(pacote.token), ignore_errors=True)
        pacote.token = None
    detalhe = f"{pacote.qtd_processos or 0} processo(s)"
    if pacote.qtd_criadas:
        detalhe += f" · {pacote.qtd_criadas} licitação(ões) criada(s)"
    lixeira.enviar([pacote], "uploads", "pacote", pacote.nome_arquivo, detalhe,
                   arquivos=[pacote.caminho_zip], usuario_id=current_user.id)
    db.session.delete(pacote)
    db.session.commit()
    msg = f"Pacote \"{pacote.nome_arquivo}\" excluído (fica 30 dias na lixeira)."
    if excluidas:
        msg += f" {excluidas} licitação(ões) criada(s) por ele também foram para a lixeira."
    if mantidas:
        msg += f" {mantidas} licitação(ões) com empenho foram mantidas."
    flash(msg, "ok")
    return redirect(url_for("imp.enviar"))


# ─── Passo 2: conferir ───────────────────────────────────────────────────────

@imp_bp.route("/<token>")
@login_required
def conferir(token):
    if not current_user.is_assessor():
        abort(403)
    analise = _ler_analise(token)
    if not analise:
        flash("Essa importação expirou ou já foi concluída. Envie o ZIP de novo.", "erro")
        return redirect(url_for("imp.enviar"))
    clientes = _clientes_do_usuario()
    nomes = {c.id: c.nome for c in clientes}
    propostas = analise["propostas"]
    for p in propostas:
        p["data_fmt"] = (datetime.strptime(p["data_disputa"], "%Y-%m-%dT%H:%M").strftime("%d/%m/%Y %H:%M")
                         if p.get("data_disputa") else None)
        p["qtd_lotes"] = len({i["lote_grupo"] for i in p["itens"] if i.get("lote_grupo")})
        p["edital_nome"] = next((a["nome"] for a in p["arquivos"] if a["tipo"] == "edital"), None)
        if p.get("cliente_sugerido") not in nomes:
            p["cliente_sugerido"] = None
    identificadas = sum(1 for p in propostas if p.get("origem_cliente") == "itens" and p.get("cliente_sugerido"))
    return render_template("importar_conferir.html", token=token, analise=analise,
                           clientes=clientes, nomes=nomes, propostas=propostas,
                           cliente_forcado=nomes.get(analise.get("cliente_id")),
                           identificadas=identificadas,
                           total_selecionadas=sum(1 for p in propostas if p["selecionada"]))


@imp_bp.route("/<token>/cancelar", methods=["POST"])
@login_required
def cancelar(token):
    if not current_user.is_assessor():
        abort(403)
    pacote = _pacote_do_token(token, _ler_analise(token))
    shutil.rmtree(_pasta_token(token), ignore_errors=True)
    if pacote:
        pacote.token = None
        if pacote.situacao != "importado":
            pacote.situacao = "descartado"
        db.session.commit()
    flash("Conferência descartada. O pacote continua no histórico, para conferir de novo ou excluir.", "ok")
    return redirect(url_for("imp.enviar"))


# ─── Passo 3: criar as licitacoes ────────────────────────────────────────────

def _aplicar_capag(lic):
    resultado = consultar_capag(lic.esfera, lic.uf, lic.municipio)
    if resultado:
        lic.capag_nota = resultado["nota"]
        lic.capag_ambito = resultado["ambito"]
        lic.capag_local = resultado["local"]
        lic.capag_referencia = resultado["referencia"]
    lic.capag_consultado_em = datetime.utcnow() if lic.esfera else None


def _criar_licitacao(cliente_id, p):
    data_disputa = None
    if p.get("data_disputa"):
        try:
            data_disputa = datetime.strptime(p["data_disputa"], "%Y-%m-%dT%H:%M")
        except ValueError:
            pass
    lic = Licitacao(
        cliente_id=cliente_id,
        orgao_licitante=(p.get("orgao_licitante") or p["pasta"])[:300],
        numero_pregao=(p.get("numero_pregao") or "s/n")[:60],
        uasg=(p.get("uasg") or "")[:30],
        portal=(p.get("portal") or "")[:100],
        data_disputa=data_disputa,
        status="agendada",
        objeto=p.get("objeto") or "",
        link_edital="",
        valor_estimado=p.get("valor_estimado"),
        codigo_busca=(p.get("codigo_busca") or None),
        esfera=p.get("esfera"),
        uf=p.get("uf"),
        municipio=p.get("municipio"),
    )
    db.session.add(lic)
    db.session.flush()  # precisa do id para a pasta de arquivos
    _aplicar_capag(lic)

    pasta_destino = os.path.join(UPLOAD_FOLDER, str(lic.id))
    os.makedirs(pasta_destino, exist_ok=True)
    for a in p["arquivos"]:
        if not os.path.exists(a["caminho"]):
            continue
        ext = os.path.splitext(a["nome"])[1]
        destino = os.path.join(pasta_destino, f"{uuid.uuid4().hex}{ext}")
        # copia (e nao move): o mesmo edital pode ir para mais de um cliente
        shutil.copy2(a["caminho"], destino)
        db.session.add(Documento(
            licitacao_id=lic.id,
            categoria="processo",
            tipo=a["tipo"],
            nome_original=a["nome"][:300],
            caminho=destino,
            tamanho=os.path.getsize(destino),
            enviado_por=current_user.id,
        ))

    for item in p.get("itens") or []:
        db.session.add(ItemLicitacao(
            licitacao_id=lic.id,
            numero_item=item.get("numero_item"),
            descricao=item.get("descricao") or "(sem descrição)",
            lote_grupo=item.get("lote_grupo"),
            unidade=item.get("unidade"),
            quantidade=item.get("quantidade"),
            valor_estimado=item.get("valor_estimado"),
        ))
    return lic


@imp_bp.route("/<token>/confirmar", methods=["POST"])
@login_required
def confirmar(token):
    if not current_user.is_assessor():
        abort(403)
    analise = _ler_analise(token)
    if not analise:
        flash("Essa importação expirou ou já foi concluída.", "erro")
        return redirect(url_for("main.painel"))
    escolhidas = set(request.form.getlist("pastas"))
    if not escolhidas:
        flash("Marque pelo menos um processo para importar.", "erro")
        return redirect(url_for("imp.conferir", token=token))
    avisar = request.form.get("avisar_email") == "1"

    # Cliente confirmado (ou trocado) em cada linha + clientes extras marcados
    destino = {}
    for i, p in enumerate(analise["propostas"]):
        if str(i) not in escolhidas:
            continue
        principal = request.form.get(f"cliente_{i}", type=int)
        extras = [int(x) for x in request.form.getlist(f"extra_{i}") if x.isdigit()]
        if not principal and extras:
            principal = extras[0]
        if not principal:
            flash(f"Escolha o cliente de \"{p.get('orgao_licitante') or p['pasta']}\".", "erro")
            return redirect(url_for("imp.conferir", token=token))
        ids = [principal]
        for extra in extras:
            if extra not in ids:
                ids.append(extra)
        for cid in ids:
            if not current_user.pode_ver_cliente(cid):
                abort(403)
        destino[i] = ids

    criadas, puladas, erros = [], [], []
    for i, p in enumerate(analise["propostas"]):
        for cliente_id in destino.get(i, []):
            if _ja_cadastrada(cliente_id, p):
                puladas.append(p["pasta"])
                continue
            try:
                lic = _criar_licitacao(cliente_id, p)
                db.session.commit()
                criadas.append(lic)
            except Exception as e:
                db.session.rollback()
                current_app.logger.exception("Falha ao importar %s", p["pasta"])
                erros.append(f"{p['pasta']}: {e}")

    if avisar and criadas:
        from app.email_service import notificar_nova_licitacao
        for lic in criadas:
            try:
                notificar_nova_licitacao(lic)
            except Exception:
                pass

    pacote = _pacote_do_token(token, analise)
    shutil.rmtree(_pasta_token(token), ignore_errors=True)
    if pacote:
        ids = pacote.ids_licitacoes + [l.id for l in criadas]
        pacote.licitacoes_ids = ",".join(str(i) for i in ids)
        pacote.qtd_criadas = len(ids)
        pacote.situacao = "importado" if ids else pacote.situacao
        pacote.importado_em = datetime.utcnow()
        pacote.token = None
        db.session.commit()

    if criadas:
        por_cliente = {}
        for lic in criadas:
            por_cliente[lic.cliente.nome] = por_cliente.get(lic.cliente.nome, 0) + 1
        detalhe = ", ".join(f"{n} para {nome}" for nome, n in sorted(por_cliente.items()))
        flash(f"{len(criadas)} licitação(ões) criada(s) a partir do pacote "
              f"\"{analise.get('nome_pacote', '')}\": {detalhe}.", "ok")
    if puladas:
        flash(f"{len(puladas)} já estava(m) cadastrada(s) e foi(ram) ignorada(s).", "ok")
    if erros:
        flash("Não consegui importar: " + " | ".join(erros), "erro")
    ids_clientes = {lic.cliente_id for lic in criadas}
    if len(ids_clientes) == 1:
        return redirect(url_for("main.painel", cliente_id=ids_clientes.pop()))
    return redirect(url_for("main.painel"))
