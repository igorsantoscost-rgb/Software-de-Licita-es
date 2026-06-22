from flask import (Blueprint, render_template, redirect, url_for,
                   request, flash, send_from_directory, abort, jsonify)
from flask_login import login_required, current_user
from app.models import (Licitacao, Documento, ItemLicitacao, Cliente,
                        STATUS_CHOICES, PORTAL_CHOICES, TIPOS_DOC_LICITACAO_UNICOS,
                        ComentarioLicitacao)
from app import db
from datetime import datetime
import os, uuid, requests

lic_bp = Blueprint("lic", __name__, url_prefix="/licitacoes")

UPLOAD_FOLDER = "/app/uploads"


def _pode_ver(licitacao):
    if current_user.is_assessor():
        return True
    return licitacao.cliente_id == current_user.cliente_id


def _salvar_arquivo(f, licitacao_id):
    """Salva um arquivo enviado e retorna o caminho no disco."""
    pasta = os.path.join(UPLOAD_FOLDER, str(licitacao_id))
    os.makedirs(pasta, exist_ok=True)
    ext = os.path.splitext(f.filename)[1]
    nome_salvo = f"{uuid.uuid4().hex}{ext}"
    caminho = os.path.join(pasta, nome_salvo)
    f.save(caminho)
    return caminho


def _processar_uploads_form(lic_id):
    """Le os arquivos do form (edital, termo_referencia, outros[]) e cria os Documentos.
    Para edital/termo_referencia, substitui o arquivo anterior se houver um novo."""
    # Slots unicos: edital, termo_referencia
    for tipo in TIPOS_DOC_LICITACAO_UNICOS:
        f = request.files.get(tipo)
        if f and f.filename:
            # remove o anterior desse tipo (slot unico, sempre o mais recente vale)
            anterior = Documento.query.filter_by(licitacao_id=lic_id, tipo=tipo).all()
            for doc_antigo in anterior:
                try:
                    os.remove(doc_antigo.caminho)
                except FileNotFoundError:
                    pass
                db.session.delete(doc_antigo)
            caminho = _salvar_arquivo(f, lic_id)
            doc = Documento(
                licitacao_id=lic_id,
                tipo=tipo,
                nome_original=f.filename,
                caminho=caminho,
                tamanho=os.path.getsize(caminho),
                enviado_por=current_user.id,
            )
            db.session.add(doc)

    # Lista livre: outros[] (varios arquivos, sem limite, nunca substitui)
    for f in request.files.getlist("outros"):
        if not f.filename:
            continue
        caminho = _salvar_arquivo(f, lic_id)
        doc = Documento(
            licitacao_id=lic_id,
            tipo="outros",
            nome_original=f.filename,
            caminho=caminho,
            tamanho=os.path.getsize(caminho),
            enviado_por=current_user.id,
        )
        db.session.add(doc)


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
            obs_cliente=request.form.get("obs_cliente", "").strip(),
        )
        db.session.add(lic)
        db.session.commit()
        _processar_uploads_form(lic.id)
        db.session.commit()
        flash("Licitacao criada.", "ok")
        return redirect(url_for("lic.detalhe", id=lic.id))
    return render_template("form_licitacao.html", clientes=clientes,
                           status_choices=STATUS_CHOICES, portal_choices=PORTAL_CHOICES, lic=None,
                           tipos_doc_unicos=TIPOS_DOC_LICITACAO_UNICOS, docs_existentes={})


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
        lic.obs_cliente = request.form.get("obs_cliente", "").strip()
        _processar_uploads_form(lic.id)
        db.session.commit()
        flash("Licitacao atualizada.", "ok")
        return redirect(url_for("lic.detalhe", id=lic.id))
    docs_existentes = {d.tipo: d for d in lic.documentos if d.tipo in TIPOS_DOC_LICITACAO_UNICOS}
    return render_template("form_licitacao.html", clientes=clientes,
                           status_choices=STATUS_CHOICES, portal_choices=PORTAL_CHOICES, lic=lic,
                           tipos_doc_unicos=TIPOS_DOC_LICITACAO_UNICOS, docs_existentes=docs_existentes)


@lic_bp.route("/<int:id>/status", methods=["POST"])
@login_required
def atualizar_status(id):
    if not current_user.is_assessor():
        abort(403)
    lic = Licitacao.query.get_or_404(id)
    novo = request.form.get("status")
    if novo not in STATUS_CHOICES:
        return redirect(url_for("lic.detalhe", id=lic.id))

    if novo == "homologada":
        valor_str = request.form.get("valor_homologado", "").strip().replace(".", "").replace(",", ".")
        if not valor_str:
            flash("Para marcar como Homologada, informe o valor total homologado.", "erro")
            return redirect(url_for("lic.detalhe", id=lic.id))
        try:
            lic.valor_homologado = float(valor_str)
        except ValueError:
            flash("Valor homologado inválido.", "erro")
            return redirect(url_for("lic.detalhe", id=lic.id))

    elif novo == "encerrada":
        motivo = request.form.get("motivo_encerramento", "").strip()
        if not motivo:
            flash("Para marcar como Encerrada, informe o motivo (ex: 2º colocado).", "erro")
            return redirect(url_for("lic.detalhe", id=lic.id))
        lic.motivo_encerramento = motivo

    lic.status = novo
    db.session.commit()
    flash(f"Status atualizado para '{novo}'.", "ok")
    return redirect(url_for("lic.detalhe", id=lic.id))


@lic_bp.route("/<int:id>/obs-cliente", methods=["POST"])
@login_required
def atualizar_obs_cliente(id):
    if not current_user.is_assessor():
        abort(403)
    lic = Licitacao.query.get_or_404(id)
    lic.obs_cliente = request.form.get("obs_cliente", "").strip()
    db.session.commit()
    flash("Observação ao cliente atualizada.", "ok")
    return redirect(url_for("lic.detalhe", id=lic.id))


# ─── Documentos ──────────────────────────────────────────────────────────────

@lic_bp.route("/<int:id>/upload", methods=["POST"])
@login_required
def upload(id):
    if not current_user.is_assessor():
        abort(403)
    lic = Licitacao.query.get_or_404(id)
    arquivos = request.files.getlist("arquivos")
    for f in arquivos:
        if not f.filename:
            continue
        caminho = _salvar_arquivo(f, id)
        doc = Documento(
            licitacao_id=id,
            tipo="outros",
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
        numero_item=request.form.get("numero_item", "").strip(),
        descricao=descricao,
        marca=request.form.get("marca", "").strip(),
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
    item.numero_item = request.form.get("numero_item", "").strip()
    item.descricao = request.form.get("descricao", item.descricao).strip()
    item.marca = request.form.get("marca", "").strip()
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


# ─── Comentários ──────────────────────────────────────────────────────────────

@lic_bp.route("/<int:id>/comentar", methods=["POST"])
@login_required
def comentar(id):
    lic = Licitacao.query.get_or_404(id)
    if not _pode_ver(lic):
        abort(403)
    texto = request.form.get("texto", "").strip()
    if not texto:
        flash("Escreva algo antes de enviar.", "erro")
        return redirect(url_for("lic.detalhe", id=id))
    comentario = ComentarioLicitacao(
        licitacao_id=id,
        autor_id=current_user.id,
        texto=texto,
    )
    db.session.add(comentario)
    db.session.commit()
    flash("Comentário enviado.", "ok")
    return redirect(url_for("lic.detalhe", id=id))


# ─── Resumo com IA ───────────────────────────────────────────────────────────

# Extensoes de PDF sao enviadas como documento nativo para a API (ela le PDF
# diretamente). As demais extensoes suportadas tem o texto extraido aqui no
# servidor antes de ir para o prompt.
EXTENSOES_TEXTO_SUPORTADAS = {".docx", ".xlsx", ".xlsm", ".csv", ".html", ".htm", ".txt"}


def _montar_conteudo_resumo(lic):
    """Monta a lista de blocos de conteudo (texto + documentos PDF nativos)
    enviados a API, a partir dos documentos anexados a licitacao
    (edital, termo_referencia, outros)."""
    from app.doc_extractor import extrair_texto_documento, ler_pdf_base64

    blocos = []
    textos_extraidos = []
    pdfs_anexados = 0
    arquivos_ignorados = []

    for doc in lic.documentos:
        if doc.tipo not in ("edital", "termo_referencia", "outros"):
            continue
        ext = os.path.splitext(doc.nome_original)[1].lower()
        if ext == ".pdf":
            b64 = ler_pdf_base64(doc)
            if b64:
                blocos.append({
                    "type": "document",
                    "source": {"type": "base64", "media_type": "application/pdf", "data": b64},
                    "title": doc.nome_original,
                })
                pdfs_anexados += 1
            else:
                arquivos_ignorados.append(doc.nome_original)
        elif ext in EXTENSOES_TEXTO_SUPORTADAS:
            texto = extrair_texto_documento(doc)
            if texto:
                textos_extraidos.append(f"\n--- Conteudo de {doc.nome_original} ---\n{texto}")
            else:
                arquivos_ignorados.append(doc.nome_original)
        else:
            arquivos_ignorados.append(doc.nome_original)

    return blocos, "\n".join(textos_extraidos), pdfs_anexados, arquivos_ignorados


@lic_bp.route("/<int:id>/resumo-ia", methods=["POST"])
@login_required
def gerar_resumo(id):
    if not current_user.is_assessor():
        abort(403)
    lic = Licitacao.query.get_or_404(id)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        flash("Chave de API da Anthropic não configurada no servidor. Avise o suporte técnico.", "erro")
        return redirect(url_for("lic.detalhe", id=id))

    palavras_chave = [p.palavra for p in lic.cliente.palavras_chave] if lic.cliente else []

    blocos_documento, texto_extraido, pdfs_anexados, arquivos_ignorados = _montar_conteudo_resumo(lic)

    if not blocos_documento and not texto_extraido:
        flash("Nenhum documento legível foi encontrado (Edital, Termo de Referência ou Outros). "
              "Anexe ao menos um arquivo PDF, DOCX, XLSX, CSV ou HTML antes de gerar o resumo.", "erro")
        return redirect(url_for("lic.detalhe", id=id))

    itens_cadastrados = "\n".join(
        f"- {item.descricao} | Marca: {item.marca or '-'} | Lote/Grupo: {item.lote_grupo or '-'} | "
        f"Qtd: {item.quantidade or '-'} {item.unidade or ''} | Valor mínimo: "
        f"{('R$ ' + str(item.valor_minimo)) if item.valor_minimo else 'não informado'}"
        for item in lic.itens
    ) or "Nenhum item cadastrado manualmente no sistema."

    palavras_txt = ", ".join(palavras_chave) if palavras_chave else "(nenhuma palavra-chave cadastrada para este cliente)"

    instrucoes = f"""Você é um especialista em licitações públicas brasileiras, ajudando uma consultoria a
analisar uma oportunidade para seu cliente.

DADOS DO PROCESSO (cadastrados no sistema):
Órgão: {lic.orgao_licitante}
Pregão: {lic.numero_pregao}
UASG: {lic.uasg or 'não informado'}
Portal: {lic.portal or 'não informado'}
Data da disputa: {lic.data_disputa.strftime('%d/%m/%Y %H:%M') if lic.data_disputa else 'não informada'}
Objeto (cadastrado): {lic.objeto or 'não informado'}

ITENS CADASTRADOS MANUALMENTE NO SISTEMA (podem ser parciais ou estar ausentes):
{itens_cadastrados}

PALAVRAS-CHAVE DE INTERESSE DESTE CLIENTE:
{palavras_txt}

Os documentos anexados (edital, termo de referência e/ou outros arquivos) estão inclusos abaixo
ou em anexo a esta mensagem. Leia-os com atenção e produza um resumo executivo em português,
direto e objetivo, com EXATAMENTE estas seções, usando estes títulos:

## Itens Relacionados ao Interesse do Cliente
Liste apenas os itens/lotes do edital ou termo de referência cuja DESCRIÇÃO contenha (mesmo que
parcialmente ou com sinônimo próximo) alguma das palavras-chave de interesse listadas acima.
Para cada item relacionado, informe: descrição, número do item/lote, quantidade e valor estimado
(se disponível no documento). Se nenhuma palavra-chave foi cadastrada para este cliente, ou se
nenhum item corresponder, diga isso explicitamente nesta seção.

## Modo de Disputa
Informe o modo de disputa (ex: aberto, fechado, aberto e fechado, menor preço, melhor técnica etc.),
se ha lotes/grupos ou itens isolados, e qualquer regra relevante de julgamento encontrada no edital.

## Prazo de Pagamento
Informe o prazo de pagamento ao fornecedor descrito no edital/termo de referência (ex: "30 dias após
liquidação da nota fiscal"). Se não encontrar essa informação no documento, diga isso explicitamente.

## Requisitos de Habilitação e Qualificação Técnica
Resuma os principais requisitos de habilitação/qualificação técnica exigidos (atestados de capacidade
técnica, registros, certidões específicas, patrimônio mínimo etc.) que sejam relevantes para a decisão
de participar.

## Pontos de Atenção
Quaisquer prazos, riscos ou exigências que mereçam atenção especial do cliente ou do assessor.

Seja direto, use bullet points quando fizer sentido, e NÃO invente informação que não esteja nos
documentos ou dados acima — se não encontrar algo, diga que não foi encontrado no documento.

FORMATAÇÃO (importante, será renderizada como Markdown):
- Use "##" para os títulos das seções acima (exatamente como escritos).
- Sempre deixe uma linha vazia antes de iniciar uma lista com "-" e uma linha vazia depois da lista.
- Não use "---" (linha horizontal) para separar itens dentro de uma lista — use apenas entre seções,
  se necessário, e sempre com linha vazia antes e depois.
- Não coloque mais de uma informação por linha de lista; cada "-" deve estar em sua própria linha,
  separada por quebra de linha real (não use "\\n" literal nem junte itens na mesma linha)."""

    if texto_extraido:
        instrucoes += f"\n\n--- TEXTO EXTRAÍDO DOS DOCUMENTOS ANEXADOS (não-PDF) ---\n{texto_extraido}"

    conteudo_mensagem = blocos_documento + [{"type": "text", "text": instrucoes}]

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "Content-Type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
            json={
                "model": "claude-sonnet-4-6",
                "max_tokens": 1500,
                "messages": [{"role": "user", "content": conteudo_mensagem}],
            },
            timeout=90,
        )
        data = resp.json()
        if resp.status_code != 200:
            erro_msg = data.get("error", {}).get("message", str(data))
            resumo = f"Erro ao gerar resumo (HTTP {resp.status_code}): {erro_msg}"
        else:
            resumo = "".join(
                bloco.get("text", "") for bloco in data.get("content", []) if bloco.get("type") == "text"
            )
            if arquivos_ignorados:
                resumo += ("\n\n---\n*Observação: os seguintes arquivos não puderam ser lidos e foram "
                          f"ignorados nesta análise: {', '.join(arquivos_ignorados)}.*")
    except Exception as e:
        resumo = f"Erro ao gerar resumo: {str(e)}"

    lic.resumo_ia = resumo
    db.session.commit()
    flash("Resumo gerado com sucesso.", "ok")
    return redirect(url_for("lic.detalhe", id=id))
