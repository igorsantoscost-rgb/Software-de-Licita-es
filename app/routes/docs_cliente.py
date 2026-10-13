from flask import Blueprint, render_template, redirect, url_for, request, flash, send_from_directory, send_file, abort, jsonify
from flask_login import login_required, current_user
from app.models import (
    Cliente, DocumentoCliente, SocioCliente, DOCUMENTOS_CLIENTE, DOCUMENTO_TIPOS,
    SETORES_DOCUMENTOS, TIPOS_MULTIPLOS, TIPOS_OPCIONAIS, SETOR_SOCIOS, TIPOS_SOCIO, TIPOS_FORA_DO_ZIP,
)
from app import db
from app import validade_doc, verifica_doc
from datetime import datetime, date
import os, uuid, io, zipfile, tempfile, json

docs_bp = Blueprint("docs", __name__, url_prefix="/clientes")

UPLOAD_FOLDER = "/app/uploads/clientes"
AVISO_DIAS = 15
TIPOS_CONTRATO = ("contrato_social", "alteracao_contratual")
ROTULOS = {slug: label for slug, label in DOCUMENTOS_CLIENTE}
ROTULOS_CURTOS = {
    "doc_socio": "Documento de identificação",
    "estado_civil": "Comprovação de estado civil",
    "doc_conjuge": "Identificação do cônjuge",
}


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


def _nome_cliente(cliente_id):
    c = Cliente.query.get(cliente_id)
    return c.nome if c else ""


def _nome_seguro(txt):
    """Nome valido para pasta/arquivo no Windows ("Contrato Social / Consolidado" -> "Contrato Social - Consolidado")."""
    import re
    txt = re.sub(r'\s*[\\/:*?"<>|]+\s*', " - ", str(txt or "")).strip(" .-")
    return re.sub(r"\s+", " ", txt) or "Documento"


def _voltar(cliente_id, ancora=None):
    # Envio pela tela (salvar varios de uma vez): responde sem redirecionar.
    # As mensagens ficam guardadas e aparecem quando a tela recarrega.
    if request.headers.get("X-Requested-With") == "fetch":
        return jsonify({"ok": True})
    return redirect(url_for("docs.documentos", cliente_id=cliente_id) + (f"#{ancora}" if ancora else ""))


# ─── Leitura e conferencia do arquivo ────────────────────────────────────────

def _analisar(caminho, nome_original, tipo, cliente, socio=None):
    """Le o arquivo uma vez: validade + conferencia de CNPJ/CPF + socios do contrato."""
    texto = verifica_doc.texto_do_arquivo(caminho, nome_original)
    r = validade_doc.ler_validade(caminho, nome_original, tipo, texto=texto)
    if tipo in TIPOS_SOCIO:
        conf = verifica_doc.conferir_socio(texto, socio, tipo)
    else:
        conf = verifica_doc.conferir_cnpj(texto, cliente.cnpj, tipo)
    socios = verifica_doc.socios_do_contrato(texto) if tipo in TIPOS_CONTRATO and texto else []
    return {
        "validade": r["validade"], "emissao": r["emissao"], "origem": r["origem"],
        "mensagem_validade": r["mensagem"], "conferencia": conf,
        "socios": socios, "texto_lido": verifica_doc.tem_texto(texto),
    }


def _contrato_vigente(cliente_id):
    """Contrato social (ou, sem ele, a alteracao mais recente)."""
    q = DocumentoCliente.query.filter(
        DocumentoCliente.cliente_id == cliente_id,
        DocumentoCliente.nao_se_aplica == False,  # noqa: E712
    )
    doc = q.filter(DocumentoCliente.tipo == "contrato_social").order_by(DocumentoCliente.criado_em.desc()).first()
    return doc or q.filter(DocumentoCliente.tipo == "alteracao_contratual").order_by(DocumentoCliente.criado_em.desc()).first()


def _socios_lidos(doc):
    """(lista de socios, conseguiu_ler) do contrato. Le o arquivo so na primeira vez."""
    if doc is None:
        return [], False
    if doc.dados_extra:
        try:
            dados = json.loads(doc.dados_extra)
            return dados.get("socios", []), dados.get("lido", False)
        except ValueError:
            pass
    texto = verifica_doc.texto_do_arquivo(doc.caminho, doc.nome_original) if os.path.exists(doc.caminho or "") else ""
    socios = verifica_doc.socios_do_contrato(texto) if texto else []
    doc.dados_extra = json.dumps({"socios": socios, "lido": verifica_doc.tem_texto(texto)}, ensure_ascii=False)
    db.session.commit()
    return socios, verifica_doc.tem_texto(texto)


def _sincronizar_socios(cliente_id):
    """Sem nenhum socio cadastrado: cria os socios lidos no contrato social.
    Com um socio so: vincula a ele os documentos de socio que estao sem dono."""
    contrato = _contrato_vigente(cliente_id)
    lidos, _ = _socios_lidos(contrato)
    socios = SocioCliente.query.filter_by(cliente_id=cliente_id).order_by(SocioCliente.id).all()
    criados = 0
    if not socios and lidos:
        for s in lidos:
            db.session.add(SocioCliente(cliente_id=cliente_id, nome=s["nome"], cpf=s["cpf"],
                                        estado_civil_contrato=s.get("estado_civil") or None))
            criados += 1
        db.session.commit()
        socios = SocioCliente.query.filter_by(cliente_id=cliente_id).order_by(SocioCliente.id).all()
    if len(socios) == 1:
        ids = {socios[0].id}
        soltos = [d for d in DocumentoCliente.query.filter(
            DocumentoCliente.cliente_id == cliente_id, DocumentoCliente.tipo.in_(TIPOS_SOCIO)).all()
            if d.socio_id not in ids]
        for d in soltos:
            d.socio_id = socios[0].id
        if soltos:
            db.session.commit()
    return criados


def _socio_do_cliente(cliente_id, socio_id):
    try:
        socio_id = int(socio_id)
    except (TypeError, ValueError):
        return None
    s = SocioCliente.query.get(socio_id)
    return s if s and s.cliente_id == cliente_id else None


# ─── Pasta de documentos ─────────────────────────────────────────────────────

@docs_bp.route("/<int:cliente_id>/documentos")
@login_required
def documentos(cliente_id):
    cliente = Cliente.query.get_or_404(cliente_id)
    if not current_user.is_assessor() and current_user.cliente_id != cliente_id:
        abort(403)

    if current_user.is_assessor():
        _sincronizar_socios(cliente_id)

    todos_docs = DocumentoCliente.query.filter_by(cliente_id=cliente_id).order_by(DocumentoCliente.criado_em.desc()).all()
    socios = SocioCliente.query.filter_by(cliente_id=cliente_id).order_by(SocioCliente.id).all()
    ids_socios = {s.id for s in socios}

    # Agrupa por tipo — tipos múltiplos viram lista; os demais pegam o mais recente
    docs_por_tipo = {}
    docs_por_socio = {s.id: {} for s in socios}
    docs_sem_socio = []
    for doc in todos_docs:
        if doc.tipo in TIPOS_SOCIO:
            if doc.socio_id in ids_socios:
                docs_por_socio[doc.socio_id].setdefault(doc.tipo, doc)
            else:
                docs_sem_socio.append(doc)
        elif doc.tipo in TIPOS_MULTIPLOS:
            docs_por_tipo.setdefault(doc.tipo, []).append(doc)
        elif doc.tipo not in docs_por_tipo:
            docs_por_tipo[doc.tipo] = doc

    pendencias, criticas = [], []

    # Documentos da empresa
    for slug, label in DOCUMENTOS_CLIENTE:
        if slug in TIPOS_OPCIONAIS or slug in TIPOS_SOCIO:
            continue
        doc = docs_por_tipo.get(slug)
        if isinstance(doc, list):
            tem_doc = len(doc) > 0 or any(d.nao_se_aplica for d in doc)
        else:
            tem_doc = doc is not None
        if not tem_doc:
            pendencias.append(label)

    # Quadro societario
    contrato = _contrato_vigente(cliente_id)
    lidos, contrato_lido = _socios_lidos(contrato)
    cpfs_cadastrados = {verifica_doc.so_digitos(s.cpf) for s in socios if s.cpf}
    cpfs_contrato = {verifica_doc.so_digitos(s["cpf"]) for s in lidos}
    faltando_cadastro = [s for s in lidos if verifica_doc.so_digitos(s["cpf"]) not in cpfs_cadastrados]

    if not socios:
        pendencias.append("Quadro societário: nenhum sócio cadastrado")
    if contrato and lidos and len(socios) < len(lidos):
        criticas.append(f"O contrato social cita {len(lidos)} sócio(s), mas só {len(socios)} está(ão) "
                        f"cadastrado(s) no quadro societário.")

    quadro = []
    for s in socios:
        docs_s = docs_por_socio.get(s.id, {})
        estado = docs_s.get("estado_civil")
        conjuge_oculto = bool(estado and estado.nao_se_aplica)
        slots = [t for t in TIPOS_SOCIO if not (t == "doc_conjuge" and conjuge_oculto)]
        for t in slots:
            if t not in docs_s:
                pendencias.append(f"{ROTULOS_CURTOS[t]} — {s.nome}")
        fora_contrato = bool(contrato_lido and lidos and s.cpf
                             and verifica_doc.so_digitos(s.cpf) not in cpfs_contrato)
        quadro.append({"socio": s, "docs": docs_s, "slots": slots,
                       "conjuge_oculto": conjuge_oculto, "fora_contrato": fora_contrato})

    # Criticas gravadas nos documentos (ex.: CNPJ de outra empresa enviado assim mesmo)
    nomes_socio = {s.id: s.nome for s in socios}
    for d in todos_docs:
        if d.alerta and not d.nao_se_aplica:
            dono = f" ({nomes_socio[d.socio_id]})" if d.socio_id in nomes_socio else ""
            criticas.append(f"{ROTULOS.get(d.tipo, d.tipo)}{dono}: {d.alerta}")

    return render_template(
        "documentos_cliente.html",
        cliente=cliente,
        setores=SETORES_DOCUMENTOS,
        setor_socios=SETOR_SOCIOS,
        tipos_multiplos=list(TIPOS_MULTIPLOS),
        tipos_fora_zip=list(TIPOS_FORA_DO_ZIP),
        docs_por_tipo=docs_por_tipo,
        pendencias=pendencias,
        criticas=criticas,
        quadro=quadro,
        socios=socios,
        docs_sem_socio=docs_sem_socio,
        contrato=contrato,
        contrato_lido=contrato_lido,
        socios_contrato=lidos,
        faltando_cadastro=faltando_cadastro,
        rotulos=ROTULOS,
        rotulos_curtos=ROTULOS_CURTOS,
        status_validade=_status_validade,
        hoje=date.today(),
        aviso_dias=AVISO_DIAS,
        tipos_sem_validade=list(validade_doc.TIPOS_SEM_VALIDADE),
        formatar_cpf=verifica_doc.formatar_cpf,
    )


@docs_bp.route("/<int:cliente_id>/documentos/baixar-zip")
@login_required
def baixar_zip(cliente_id):
    cliente = Cliente.query.get_or_404(cliente_id)
    if not current_user.is_assessor() and current_user.cliente_id != cliente_id:
        abort(403)

    docs = (DocumentoCliente.query
            .filter_by(cliente_id=cliente_id)
            .order_by(DocumentoCliente.criado_em)
            .all())
    nomes_socio = {s.id: s.nome for s in SocioCliente.query.filter_by(cliente_id=cliente_id).all()}
    from app.certificado import e_certificado

    # Uma pasta por nivel de habilitacao, na ordem da tela ("1 - Regularidade Fiscal"...)
    pasta_do_tipo = {}
    setores_zip = [(st, ds) for st, ds in SETORES_DOCUMENTOS
                   if not all(slug in TIPOS_FORA_DO_ZIP for slug, _ in ds)]
    for i, (setor, docs_setor) in enumerate(setores_zip, start=1):
        for slug, _label in docs_setor:
            pasta_do_tipo[slug] = f"{i} - {_nome_seguro(setor)}"

    buf = io.BytesIO()
    adicionados = 0
    certificados = 0
    usados = set()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for d in docs:
            if d.nao_se_aplica or not d.caminho or not os.path.exists(d.caminho):
                continue
            # Contrato de consultoria e aditivos: internos, nunca vao para o ZIP
            if d.tipo in TIPOS_FORA_DO_ZIP:
                continue
            # Certificado digital nunca vai para o ZIP
            if e_certificado(d.nome_original, d.caminho, caminho=d.caminho):
                certificados += 1
                continue
            pasta = pasta_do_tipo.get(d.tipo, "Outros")
            nome = ROTULOS.get(d.tipo, d.tipo)
            if d.tipo in TIPOS_SOCIO:
                dono = nomes_socio.get(d.socio_id, "Sem sócio vinculado")
                pasta = f"{pasta}/{_nome_seguro(dono)}"
                nome = ROTULOS_CURTOS[d.tipo]
            ext = os.path.splitext(d.nome_original)[1] or os.path.splitext(d.caminho)[1]
            if d.tipo == "outros_documentos" and d.nome_original:
                nome = os.path.splitext(d.nome_original)[0]  # mantem o nome do arquivo enviado
            base = _nome_seguro(nome)
            destino, n = f"{pasta}/{base}{ext}", 2
            # Evita nomes repetidos quando há vários do mesmo tipo
            while destino.lower() in usados:
                destino, n = f"{pasta}/{base} ({n}){ext}", n + 1
            usados.add(destino.lower())
            zf.write(d.caminho, destino)
            adicionados += 1

    if certificados:
        flash(f"{certificados} certificado(s) digital(is) ficaram fora do ZIP: certificado nunca é baixado em ZIP.", "erro")
    if adicionados == 0:
        flash("Nenhum documento disponível para baixar.", "erro")
        return _voltar(cliente_id)

    buf.seek(0)
    data_hoje = date.today().strftime("%d-%m-%Y")
    nome_zip = f"Habilitação - {cliente.nome} - {data_hoje}.zip"
    return send_file(buf, mimetype="application/zip",
                     as_attachment=True, download_name=nome_zip)


@docs_bp.route("/<int:cliente_id>/documentos/analisar", methods=["POST"])
@login_required
def analisar(cliente_id):
    """Le o arquivo escolhido antes de salvar: validade e conferencia de CNPJ/CPF."""
    if not current_user.is_assessor():
        abort(403)
    cliente = Cliente.query.get_or_404(cliente_id)
    arquivo = request.files.get("arquivo")
    tipo = request.form.get("tipo", "")
    socio = _socio_do_cliente(cliente_id, request.form.get("socio_id"))
    if not arquivo or not arquivo.filename:
        return jsonify({"ok": False, "mensagem": "Nenhum arquivo."}), 400
    ext = os.path.splitext(arquivo.filename)[1].lower()
    fd, tmp = tempfile.mkstemp(suffix=ext)
    os.close(fd)
    try:
        arquivo.save(tmp)
        r = _analisar(tmp, arquivo.filename, tipo, cliente, socio)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    return jsonify({
        "ok": True,
        "validade": r["validade"].isoformat() if r["validade"] else "",
        "emissao": r["emissao"].isoformat() if r["emissao"] else "",
        "origem": r["origem"],
        "mensagem": r["mensagem_validade"],
        "conferencia": r["conferencia"],
        "socios": r["socios"],
    })


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
    sem_validade = request.form.get("sem_validade") == "on"
    confirmou = request.form.get("confirmar_divergencia") == "on"
    validade = None
    if validade_str:
        try:
            validade = datetime.strptime(validade_str, "%Y-%m-%d").date()
        except ValueError:
            pass

    if tipo not in DOCUMENTO_TIPOS:
        flash("Tipo de documento inválido.", "erro")
        return _voltar(cliente_id)

    socio = None
    ancora = f"doc-{tipo}"
    if tipo in TIPOS_SOCIO:
        socio = _socio_do_cliente(cliente_id, request.form.get("socio_id"))
        if socio is None:
            flash("Escolha o sócio deste documento.", "erro")
            return _voltar(cliente_id, "quadro-societario")
        ancora = f"socio-{socio.id}"

    if nao_aplica and not obs:
        flash(f"{ROTULOS.get(tipo, tipo)}: escreva o motivo do \"não se aplica\".", "erro")
        return _voltar(cliente_id, ancora)

    def _anteriores():
        q = DocumentoCliente.query.filter_by(cliente_id=cliente_id, tipo=tipo)
        if socio is not None:
            q = q.filter_by(socio_id=socio.id)
        return q.all()

    arquivo = request.files.get("arquivo")
    from app import lixeira

    if nao_aplica:
        # Remove docs anteriores do mesmo tipo e registra nao_se_aplica
        if tipo not in TIPOS_MULTIPLOS:
            for a in _anteriores():
                if a.caminho and not a.nao_se_aplica:
                    lixeira.enviar_documento(a, "documento_cliente", f"{_nome_cliente(a.cliente_id)} · substituído por \"não se aplica\"",
                                             usuario_id=current_user.id)
                db.session.delete(a)
        doc = DocumentoCliente(
            cliente_id=cliente_id,
            tipo=tipo,
            nome_original="N/A",
            caminho="",
            nao_se_aplica=True,
            obs=obs,
            validade=None,
            enviado_por=current_user.id,
            socio_id=socio.id if socio else None,
        )
        db.session.add(doc)
        db.session.commit()
        if tipo == "estado_civil":
            flash(f"Comprovação de estado civil de {socio.nome}: não se aplica. O espaço do cônjuge foi retirado.", "ok")
        else:
            flash("Marcado como não se aplica.", "ok")
        return _voltar(cliente_id, ancora)

    if not arquivo or not arquivo.filename:
        flash("Selecione um arquivo.", "erro")
        return _voltar(cliente_id, ancora)

    pasta = os.path.join(UPLOAD_FOLDER, str(cliente_id))
    os.makedirs(pasta, exist_ok=True)
    ext = os.path.splitext(arquivo.filename)[1]
    nome_salvo = f"{uuid.uuid4().hex}{ext}"
    caminho = os.path.join(pasta, nome_salvo)
    arquivo.save(caminho)

    analise = _analisar(caminho, arquivo.filename, tipo, cliente, socio)
    conf = analise["conferencia"]

    # Documento de outra empresa/pessoa: so salva com a confirmacao do assessor
    if conf["nivel"] == "erro" and not confirmou:
        os.remove(caminho)
        flash(f"Documento NÃO salvo. {conf['mensagem']} Se estiver certo, escolha o arquivo de novo e marque "
              f"\"Enviar mesmo assim\".", "erro")
        return _voltar(cliente_id, ancora)

    # Data em branco: le do documento (ou emissao + 90 dias)
    validade_auto = None
    if not validade and not sem_validade and analise["validade"]:
        validade = analise["validade"]
        validade_auto = analise["mensagem_validade"]

    # Para tipos únicos, remove o anterior
    if tipo not in TIPOS_MULTIPLOS:
        for a in _anteriores():
            if a.caminho and not a.nao_se_aplica:
                lixeira.enviar_documento(a, "documento_cliente", f"{_nome_cliente(a.cliente_id)} · substituído por um arquivo novo",
                                         usuario_id=current_user.id)
            db.session.delete(a)

    alerta = None
    if conf["nivel"] == "erro":
        alerta = conf["mensagem"] + " (enviado assim mesmo pelo assessor)"
    elif conf["nivel"] == "aviso" and "outro estabelecimento" in conf["mensagem"]:
        alerta = conf["mensagem"]

    dados_extra = None
    if tipo in TIPOS_CONTRATO:
        dados_extra = json.dumps({"socios": analise["socios"], "lido": analise["texto_lido"]}, ensure_ascii=False)

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
        socio_id=socio.id if socio else None,
        alerta=alerta,
        dados_extra=dados_extra,
    )
    db.session.add(doc)
    db.session.commit()

    partes = ["Documento enviado."]
    if validade_auto:
        partes.append(validade_auto)
    if conf["nivel"] in ("ok", "aviso") and conf["mensagem"]:
        partes.append(conf["mensagem"])
    if tipo in TIPOS_CONTRATO:
        criados = _sincronizar_socios(cliente_id)
        n = len(analise["socios"])
        if criados:
            partes.append(f"Encontrei {n} sócio(s) no contrato e criei o quadro societário.")
        elif n:
            partes.append(f"Encontrei {n} sócio(s) no contrato. Confira o quadro societário.")
        elif analise["texto_lido"]:
            partes.append("Não encontrei os sócios no contrato. Cadastre-os no quadro societário.")
        else:
            partes.append("Contrato escaneado: cadastre os sócios no quadro societário.")
    flash(" ".join(partes), "erro" if conf["nivel"] == "erro" else "ok")
    return _voltar(cliente_id, ancora)


# ─── Socios ──────────────────────────────────────────────────────────────────

@docs_bp.route("/<int:cliente_id>/socios/adicionar", methods=["POST"])
@login_required
def adicionar_socio(cliente_id):
    if not current_user.is_assessor():
        abort(403)
    Cliente.query.get_or_404(cliente_id)
    existentes = {verifica_doc.so_digitos(s.cpf) for s in SocioCliente.query.filter_by(cliente_id=cliente_id).all() if s.cpf}

    # Vários de uma vez (lista lida do contrato) ou um digitado
    novos = []
    for item in request.form.getlist("do_contrato"):
        try:
            s = json.loads(item)
            novos.append((s["nome"], s["cpf"], s.get("estado_civil")))
        except (ValueError, KeyError, TypeError):
            continue
    nome = request.form.get("nome", "").strip()
    if nome:
        novos.append((nome, request.form.get("cpf", "").strip(), None))
    if not novos:
        flash("Informe o nome do sócio.", "erro")
        return _voltar(cliente_id, "quadro-societario")

    adicionados = 0
    for nome, cpf, estado in novos:
        d = verifica_doc.so_digitos(cpf)
        if d and not verifica_doc.cpf_valido(d):
            flash(f"CPF inválido para {nome}: {cpf}.", "erro")
            continue
        if d and d in existentes:
            flash(f"{nome} já está no quadro societário.", "erro")
            continue
        db.session.add(SocioCliente(cliente_id=cliente_id, nome=nome[:200],
                                    cpf=verifica_doc.formatar_cpf(d) if d else None,
                                    estado_civil_contrato=estado or None))
        existentes.add(d)
        adicionados += 1
    db.session.commit()
    if adicionados:
        flash(f"{adicionados} sócio(s) adicionado(s) ao quadro societário.", "ok")
    return _voltar(cliente_id, "quadro-societario")


@docs_bp.route("/socio/<int:socio_id>/editar", methods=["POST"])
@login_required
def editar_socio(socio_id):
    if not current_user.is_assessor():
        abort(403)
    s = SocioCliente.query.get_or_404(socio_id)
    nome = request.form.get("nome", "").strip()
    d = verifica_doc.so_digitos(request.form.get("cpf", ""))
    if not nome:
        flash("Informe o nome do sócio.", "erro")
    elif d and not verifica_doc.cpf_valido(d):
        flash("CPF inválido.", "erro")
    else:
        s.nome = nome[:200]
        s.cpf = verifica_doc.formatar_cpf(d) if d else None
        db.session.commit()
        flash("Sócio atualizado.", "ok")
    return _voltar(s.cliente_id, f"socio-{s.id}")


@docs_bp.route("/socio/<int:socio_id>/excluir", methods=["POST"])
@login_required
def excluir_socio(socio_id):
    if not current_user.is_assessor():
        abort(403)
    s = SocioCliente.query.get_or_404(socio_id)
    cliente_id, nome_socio = s.cliente_id, s.nome
    from app import lixeira
    for d in DocumentoCliente.query.filter_by(cliente_id=cliente_id, socio_id=s.id).all():
        if d.caminho and not d.nao_se_aplica:
            lixeira.enviar_documento(d, "documento_cliente", f"{_nome_cliente(cliente_id)} · sócio {nome_socio} removido",
                                     usuario_id=current_user.id)
        db.session.delete(d)
    db.session.delete(s)
    db.session.commit()
    flash(f"Sócio {nome_socio} removido do quadro societário (documentos foram para a lixeira).", "ok")
    return _voltar(cliente_id, "quadro-societario")


@docs_bp.route("/doc-cliente/<int:doc_id>/vincular", methods=["POST"])
@login_required
def vincular_socio(doc_id):
    """Documento de sócio enviado antes do quadro societário: escolhe de quem é."""
    if not current_user.is_assessor():
        abort(403)
    doc = DocumentoCliente.query.get_or_404(doc_id)
    socio = _socio_do_cliente(doc.cliente_id, request.form.get("socio_id"))
    if socio is None or doc.tipo not in TIPOS_SOCIO:
        flash("Escolha o sócio.", "erro")
        return _voltar(doc.cliente_id, "quadro-societario")
    from app import lixeira
    for a in DocumentoCliente.query.filter_by(cliente_id=doc.cliente_id, tipo=doc.tipo, socio_id=socio.id).all():
        if a.id == doc.id:
            continue
        if a.caminho and not a.nao_se_aplica:
            lixeira.enviar_documento(a, "documento_cliente", f"{_nome_cliente(a.cliente_id)} · substituído",
                                     usuario_id=current_user.id)
        db.session.delete(a)
    doc.socio_id = socio.id
    db.session.commit()
    flash(f"Documento vinculado a {socio.nome}.", "ok")
    return _voltar(doc.cliente_id, f"socio-{socio.id}")


# ─── Arquivos ────────────────────────────────────────────────────────────────

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
    ancora = f"socio-{doc.socio_id}" if doc.socio_id else f"doc-{doc.tipo}"
    if doc.caminho and not doc.nao_se_aplica:
        from app import lixeira
        lixeira.enviar_documento(doc, "documento_cliente", _nome_cliente(doc.cliente_id),
                                 usuario_id=current_user.id)
    db.session.delete(doc)
    db.session.commit()
    flash("Documento removido.", "ok")
    return _voltar(cliente_id, ancora)
