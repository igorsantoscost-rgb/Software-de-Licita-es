"""
Importacao em lote: o assessor sobe o HTML exportado da Effecti (ou o ZIP
antigo do sistema de busca), confere a lista de processos encontrados e o
Bidfy cria uma licitacao por processo (com os arquivos, os itens e os campos
basicos ja preenchidos).
"""
import json
import os
import re
import shutil
import threading
import time
import uuid
from datetime import datetime

from flask import (Blueprint, render_template, redirect, url_for, request,
                   flash, abort, current_app, send_file)
from flask_login import login_required, current_user

from app import db
from app.models import (Licitacao, Documento, ItemLicitacao, Cliente,
                        CapagMunicipio, PacoteImportado, User)
from app.capag import consultar as consultar_capag, normalizar
from app.importador_pacote import extrair_zip, analisar_pacote, BaseMunicipios
from app.importador_html import analisar_html
from app.classificador_cliente import Classificador

imp_bp = Blueprint("imp", __name__, url_prefix="/licitacoes/importar")

UPLOAD_FOLDER = "/app/uploads"
PASTA_IMPORTACOES = os.path.join(UPLOAD_FOLDER, "_importacoes")
PASTA_PACOTES = os.path.join(UPLOAD_FOLDER, "_pacotes")  # arquivos originais (historico)
EXTENSOES_PACOTE = (".html", ".htm", ".zip")
HORAS_PARA_LIMPAR = 48
MINUTOS_MAX_LEITURA = 20  # leitura "processando" ha mais tempo que isso travou (ex: container reiniciou)


def _clientes_do_usuario():
    return _clientes_de(current_user)


def _clientes_de(usuario):
    if usuario.is_master():
        return Cliente.query.order_by(Cliente.nome).all()
    return usuario.clientes_atendidos.order_by(Cliente.nome).all()


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
    return Classificador([(c.id, c.nome, [p.palavra for p in c.palavras_chave],
                           [p.palavra for p in c.palavras_exclusao]) for c in clientes])


def _cliente_do_perfil(perfil, clientes):
    """Perfil da Effecti (ex: "MERIDIAN") -> cliente cujo nome tem essa palavra.
    So devolve se for um cliente so."""
    n = normalizar(perfil or "")
    if len(n) < 3:
        return None
    rx = re.compile(r"\b" + re.escape(n) + r"\b")
    achados = [c.id for c in clientes if rx.search(normalizar(c.nome))]
    return achados[0] if len(achados) == 1 else None


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
        nome_enviado = (arquivo.filename or "").lower() if arquivo else ""
        if not nome_enviado.endswith(EXTENSOES_PACOTE):
            flash("Envie o arquivo .html exportado da Effecti (ou o .zip do sistema antigo).", "erro")
            return redirect(url_for("imp.enviar"))

        # O arquivo original fica guardado e registrado no historico de pacotes
        os.makedirs(PASTA_PACOTES, exist_ok=True)
        ext = ".zip" if nome_enviado.endswith(".zip") else ".html"
        caminho_zip = os.path.join(PASTA_PACOTES, f"{uuid.uuid4().hex}{ext}")
        arquivo.save(caminho_zip)
        pacote = PacoteImportado(nome_arquivo=(arquivo.filename or f"pacote{ext}")[:300],
                                 caminho_zip=caminho_zip, tamanho=os.path.getsize(caminho_zip),
                                 enviado_por=current_user.id, situacao="processando")
        db.session.add(pacote)
        db.session.commit()
        return _iniciar_leitura(pacote, cliente_forcado)

    pacotes = PacoteImportado.query.order_by(PacoteImportado.enviado_em.desc()).limit(200).all()
    for pc in pacotes:
        pc.conferencia_aberta = bool(pc.token and _ler_analise(pc.token))
    sem_palavras = [c for c in clientes if not c.palavras_chave]
    return render_template("importar_pacote.html", clientes=clientes, sem_palavras=sem_palavras,
                           pacotes=pacotes)


# ─── Leitura em segundo plano ────────────────────────────────────────────────
# Baixar os anexos de muitas licitacoes leva minutos, e o nginx corta a pagina
# em 60 s (504). Por isso a leitura roda numa thread e a tela "processando"
# se atualiza sozinha ate a conferencia ficar pronta.

def _arq_erro(pacote):
    return f"{pacote.caminho_zip}.erro"


def _arq_inicio(pacote):
    return f"{pacote.caminho_zip}.lendo"


def _leitura_travada(pacote):
    try:
        return time.time() - os.path.getmtime(_arq_inicio(pacote)) > MINUTOS_MAX_LEITURA * 60
    except OSError:
        return True


def _iniciar_leitura(pacote, cliente_forcado):
    for arq in (_arq_erro(pacote),):
        try:
            os.remove(arq)
        except OSError:
            pass
    with open(_arq_inicio(pacote), "w") as f:
        f.write(datetime.now().isoformat())
    pacote.situacao = "processando"
    pacote.token = None
    db.session.commit()
    app = current_app._get_current_object()
    threading.Thread(target=_ler_em_segundo_plano,
                     args=(app, pacote.id, cliente_forcado, current_user.id),
                     daemon=True).start()
    return redirect(url_for("imp.processando", id=pacote.id))


def _ler_em_segundo_plano(app, pacote_id, cliente_forcado, usuario_id):
    with app.app_context():
        pacote = None
        try:
            pacote = PacoteImportado.query.get(pacote_id)
            usuario = User.query.get(usuario_id)
            erro = _analisar_e_conferir(pacote, cliente_forcado, _clientes_de(usuario), usuario_id)
        except Exception as e:
            app.logger.exception("Falha na leitura do pacote %s", pacote_id)
            db.session.rollback()
            erro = f"Não consegui ler o arquivo: {e}"
            if pacote is not None:
                pacote.situacao = "com erro"
                db.session.commit()
        if erro and pacote is not None:
            with open(_arq_erro(pacote), "w", encoding="utf-8") as f:
                f.write(erro)
        try:
            if pacote is not None:
                os.remove(_arq_inicio(pacote))
        except OSError:
            pass
        db.session.remove()


@imp_bp.route("/pacote/<int:id>/processando")
@login_required
def processando(id):
    if not current_user.is_assessor():
        abort(403)
    pacote = PacoteImportado.query.get_or_404(id)
    if pacote.situacao == "processando":
        if not _leitura_travada(pacote):
            return render_template("importar_processando.html", pacote=pacote)
        pacote.situacao = "com erro"
        db.session.commit()
        flash("A leitura parou no meio (o sistema pode ter reiniciado). Clique em \"Conferir de novo\".", "erro")
        return redirect(url_for("imp.enviar"))
    if pacote.token and _ler_analise(pacote.token):
        return redirect(url_for("imp.conferir", token=pacote.token))
    erro = None
    try:
        with open(_arq_erro(pacote), encoding="utf-8") as f:
            erro = f.read().strip()
    except OSError:
        pass
    flash(erro or "Não consegui ler o arquivo.", "erro")
    return redirect(url_for("imp.enviar"))


def _participacao_e_capag(p):
    """Preenche na proposta a nota CAPAG e a participacao ME/EPP."""
    from app.participacao import classificar
    from app.importador_pacote import texto_inicio
    try:
        p["capag_nota"] = (consultar_capag(p.get("esfera"), p.get("uf"), p.get("municipio")) or {}).get("nota")
    except Exception:
        p["capag_nota"] = None
    textos = [p.get("objeto") or ""]
    for a in p.get("arquivos") or []:
        if a.get("tipo") in ("edital", "termo_referencia") and os.path.exists(a.get("caminho") or ""):
            try:
                textos.append(texto_inicio(a["caminho"], max_chars=20000))
            except Exception:
                pass
    p["participacao_me"], p["participacao_fonte"] = classificar(p.get("itens"), *textos)


def _valor_da_licitacao(p):
    """Valor estimado da licitacao: o do edital ou, se nao tiver, a soma dos
    itens (quantidade x valor unitario). None se nao der para saber."""
    if p.get("valor_estimado"):
        return float(p["valor_estimado"])
    itens = p.get("itens") or []
    if not itens or any(not i.get("quantidade") or not i.get("valor_estimado") for i in itens):
        return None
    return round(sum(float(i["quantidade"]) * float(i["valor_estimado"]) for i in itens), 2)


def _reais(v):
    return "R$ " + f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _motivos_fora_do_perfil(cliente, p):
    """Por que o cliente nao participa desta licitacao (lista vazia = participa)."""
    motivos = []
    if not cliente:
        return motivos
    nota = (p.get("capag_nota") or "").upper()
    if nota and nota in cliente.lista_capag_bloqueadas:
        motivos.append(f"CAPAG {nota}")
    if cliente.bloquear_exclusiva_me and p.get("participacao_me") == "exclusiva":
        motivos.append("exclusiva ME/EPP")
    minimo = float(cliente.valor_minimo_estimado or 0)
    valor = _valor_da_licitacao(p)
    if minimo and valor is not None and valor < minimo:
        motivos.append(f"valor estimado {_reais(valor)}, abaixo do mínimo de {_reais(minimo)}")
    esfera = p.get("esfera")
    regra = cliente.esferas.get(esfera) if esfera else None
    if regra:
        uf = p.get("uf") or p.get("uf_orgao")
        if not regra["participa"]:
            motivos.append(f"não participa de licitação {ROTULO_ESFERA[esfera]}")
        elif regra["ufs"] and uf and uf not in regra["ufs"]:
            motivos.append(f"{ROTULO_ESFERA[esfera]} em {uf} (só participa em {', '.join(regra['ufs'])})")
    return motivos


ROTULO_ESFERA = {"federal": "federal", "estadual": "estadual", "municipal": "municipal"}


def _pendencias(cliente, p):
    """Regras do cliente que nao deu para conferir (falta dado na licitacao)."""
    falta = []
    if cliente.valor_minimo_estimado and _valor_da_licitacao(p) is None:
        falta.append(f"valor estimado não identificado (mínimo de {_reais(float(cliente.valor_minimo_estimado))})")
    if cliente.tem_regra_esfera:
        esfera = p.get("esfera")
        regra = cliente.esferas.get(esfera) if esfera else None
        if not esfera:
            falta.append("esfera não identificada (o cliente tem regra de esfera/estado)")
        elif regra and regra["participa"] and regra["ufs"] and not (p.get("uf") or p.get("uf_orgao")):
            falta.append(f"estado não identificado ({ROTULO_ESFERA[esfera]} só em {', '.join(regra['ufs'])})")
    return falta


def _aplicar_restricoes(p, classificador_resultado, mapa_clientes):
    """Cliente sugerido fora do perfil (CAPAG/ME-EPP): troca por outro cliente
    que combine com os itens ou desmarca a licitacao, com o motivo."""
    cid = p.get("cliente_sugerido")
    cliente = mapa_clientes.get(cid)
    motivos = _motivos_fora_do_perfil(cliente, p)
    if not motivos:
        falta = _pendencias(cliente, p) if cliente else []
        if falta:
            p["avisos"].insert(0, f"Confira o perfil de {cliente.nome}: " + "; ".join(falta) + ".")
        return
    nome = mapa_clientes[cid].nome
    alternativo = None
    if p.get("origem_cliente") == "itens":
        for r in (classificador_resultado or {}).get("ranking") or []:
            if (r["cliente_id"] != cid and r.get("itens") and not r.get("so_grupo_misto")
                    and not _motivos_fora_do_perfil(mapa_clientes.get(r["cliente_id"]), p)):
                alternativo = r
                break
    texto = " e ".join(motivos)
    if alternativo:
        p["cliente_sugerido"] = alternativo["cliente_id"]
        p["avisos"].insert(0, f"Fora do perfil de {nome} ({texto}): sugeri {alternativo['nome']}.")
    else:
        p["selecionada"] = False
        p["fora_do_perfil"] = texto
        p["avisos"].insert(0, f"Fora do perfil de {nome}: {texto}. Desmarquei.")


def _analisar_e_conferir(pacote, cliente_forcado, clientes, usuario_id):
    """Le o pacote guardado (HTML da Effecti ou ZIP), monta as propostas e
    deixa a conferencia pronta. Devolve a mensagem de erro, ou None se deu certo."""
    _limpar_importacoes_antigas()
    token = uuid.uuid4().hex
    pasta = _pasta_token(token)
    os.makedirs(pasta, exist_ok=True)
    eh_html = not pacote.caminho_zip.lower().endswith(".zip")
    try:
        if eh_html:
            with open(pacote.caminho_zip, "rb") as f:
                conteudo = f.read()
            # baixa os anexos de cada licitacao para a pasta da conferencia
            analise = analisar_html(conteudo, os.path.join(pasta, "anexos"), _base_municipios())
        else:
            raiz = extrair_zip(pacote.caminho_zip, os.path.join(pasta, "extraido"))
            analise = analisar_pacote(raiz, _base_municipios())
    except Exception as e:
        current_app.logger.exception("Falha ao ler o pacote %s", pacote.nome_arquivo)
        shutil.rmtree(pasta, ignore_errors=True)
        pacote.situacao = "com erro"
        db.session.commit()
        return f"Não consegui ler o arquivo: {e}"

    if not analise["propostas"]:
        shutil.rmtree(pasta, ignore_errors=True)
        pacote.situacao = "com erro"
        db.session.commit()
        return ("Não achei nenhuma licitação no HTML." if eh_html
                else "O ZIP não tem nenhuma pasta de processo dentro.")

    classificador = _classificador(clientes)
    mapa_clientes = {c.id: c for c in clientes}
    for p in analise["propostas"]:
        # Sem itens (a Effecti nem sempre traz), compara as palavras-chave com o objeto
        itens_classif = p.get("itens")
        p["classificou_por"] = "itens"
        if not itens_classif and eh_html and p.get("objeto"):
            itens_classif = [{"descricao": p["objeto"]}]
            p["classificou_por"] = "objeto"
        resultado = classificador.classificar(itens_classif, so_nome=p["classificou_por"] == "itens")
        p["classificacao"] = resultado
        p["grupo_misto"] = []
        do_perfil = None if cliente_forcado else _cliente_do_perfil(p.get("perfil"), clientes)
        if do_perfil:
            # O perfil da Effecti so desempata: vale apenas se as palavras-chave
            # do cliente do perfil aparecem nos itens/objeto
            r_perfil = next((r for r in resultado["ranking"] if r["cliente_id"] == do_perfil), None)
            validos = [r for r in resultado["ranking"] if not r.get("so_grupo_misto")]
            melhor = max((r["itens"] for r in validos), default=0)
            # ... e so desempata: o cliente do perfil precisa ter tantos itens quanto o melhor
            if (not r_perfil or not r_perfil.get("itens") or r_perfil.get("so_grupo_misto")
                    or r_perfil["itens"] < melhor):
                do_perfil = None
        if cliente_forcado or do_perfil:
            p["cliente_sugerido"] = cliente_forcado or do_perfil
            p["origem_cliente"] = "escolhido" if cliente_forcado else "perfil"
            mistos = classificador.analisar_cliente(p["cliente_sugerido"], p.get("itens"))
            if mistos:
                p["grupo_misto"] = mistos
                forcado = next((r for r in resultado["ranking"] if r["cliente_id"] == p["cliente_sugerido"]), None)
                if forcado and forcado["so_grupo_misto"]:
                    p["selecionada"] = False
                    p["avisos"].insert(0, "Grupo misto: os itens do cliente só aparecem em grupo com itens "
                                          "fora do ramo dele. Desmarquei.")
        else:
            p["cliente_sugerido"] = resultado["cliente_id"]
            p["origem_cliente"] = "itens" if resultado["cliente_id"] else None
            escolhido = next((r for r in resultado["ranking"] if r["cliente_id"] == resultado["cliente_id"]), None)
            if escolhido and escolhido["mistos"]:
                p["grupo_misto"] = escolhido["mistos"]
            bloqueados = resultado.get("bloqueados") or []
            if not resultado["itens_total"]:
                p["avisos"].insert(0, "Sem itens nem objeto para comparar: escolha o cliente.")
            elif not resultado["cliente_id"] and bloqueados:
                # So tinha cliente em grupo misto: nao apresentar
                p["selecionada"] = False
                p["grupo_misto"] = bloqueados[0]["mistos"]
                nomes_bloq = ", ".join(r["nome"] for r in bloqueados)
                p["avisos"].insert(0, f"Grupo misto: {nomes_bloq} só tem item em grupo com itens fora do ramo. "
                                      "Não dá para disputar só a parte do cliente. Desmarquei.")
            elif not resultado["cliente_id"]:
                onde = "no objeto" if p["classificou_por"] == "objeto" else "nos itens"
                p["selecionada"] = False
                p["sem_cliente"] = True
                p["avisos"].insert(0, f"Nenhuma palavra-chave de cliente {onde}. Desmarquei; se quiser, escolha o cliente e marque.")
            elif resultado["empate"]:
                p["avisos"].insert(0, "Empate entre clientes: confira o cliente sugerido.")
        _participacao_e_capag(p)
        if p["cliente_sugerido"]:
            _aplicar_restricoes(p, resultado, mapa_clientes)
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
    analise["criado_por"] = usuario_id
    analise["criado_em"] = datetime.now().isoformat()
    with open(os.path.join(pasta, "analise.json"), "w", encoding="utf-8") as f:
        json.dump(analise, f, ensure_ascii=False)
    pacote.token = token
    pacote.situacao = "importado" if pacote.ids_licitacoes else "em conferencia"
    pacote.qtd_processos = len(analise["propostas"])
    db.session.commit()
    return None


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
    if pacote.situacao == "processando" and not _leitura_travada(pacote):
        return redirect(url_for("imp.processando", id=pacote.id))
    if pacote.token and _ler_analise(pacote.token):
        return redirect(url_for("imp.conferir", token=pacote.token))
    if not pacote.caminho_zip or not os.path.exists(pacote.caminho_zip):
        flash("O arquivo deste pacote não está mais guardado.", "erro")
        return redirect(url_for("imp.enviar"))
    return _iniciar_leitura(pacote, None)


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
        flash("Essa importação expirou ou já foi concluída. Envie o arquivo de novo.", "erro")
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
    identificadas = sum(1 for p in propostas if p.get("origem_cliente") in ("itens", "perfil") and p.get("cliente_sugerido"))
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
        link_edital=(p.get("link_edital") or "")[:500],
        valor_estimado=p.get("valor_estimado"),
        codigo_busca=(p.get("codigo_busca") or None),
        esfera=p.get("esfera"),
        uf=p.get("uf"),
        municipio=p.get("municipio"),
        participacao_me=p.get("participacao_me"),
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


def _motivo_desmarcada(p):
    """(categoria, motivo) de uma licitacao do pacote que nao foi marcada."""
    avisos = p.get("avisos") or []
    if p.get("selecionada", True):
        alerta = next((a for a in avisos if "palavra-chave" in a or "Empate" in a or "Sem itens" in a), None)
        return "Desmarcada na conferência", "Você desmarcou na conferência." + (f" (Aviso: {alerta})" if alerta else "")
    if p.get("fora_do_perfil"):
        texto = next((a for a in avisos if a.startswith("Fora do perfil")), f"Fora do perfil: {p['fora_do_perfil']}")
        return "Fora do perfil do cliente", texto.replace(" Desmarquei.", "")
    if p.get("ja_cadastrada"):
        return "Já cadastrada", "Já estava cadastrada para este cliente."
    if p.get("duplicada_de"):
        return "Repetida no pacote", f"Repetida de \"{p['duplicada_de']}\"."
    if p.get("situacao_data") == "passada":
        return "Disputa já passou", "A data da disputa já passou."
    if p.get("sem_cliente"):
        return "Sem palavra-chave de cliente", next((x for x in avisos if x.startswith("Nenhuma palavra-chave")),
                                                     "Nenhuma palavra-chave de cliente.").split(". Desmarquei")[0] + "."
    misto = next((a for a in avisos if a.startswith("Grupo misto")), None)
    if misto:
        return "Grupo misto", misto.replace(" Desmarquei.", "")
    return "Desmarcada na conferência", avisos[0] if avisos else "Desmarcada na conferência."


def _rotulo(p):
    return {"orgao": p.get("orgao_licitante") or p.get("pasta"), "numero": p.get("numero_pregao"),
            "data": (p.get("data_disputa") or "")[:10], "objeto": (p.get("objeto") or "")[:160]}


@imp_bp.route("/pacote/<int:id>/resumo")
@login_required
def resumo(id):
    """O que aconteceu com cada licitacao do pacote: criadas e nao criadas (motivo)."""
    if not current_user.is_assessor():
        abort(403)
    pacote = PacoteImportado.query.get_or_404(id)
    r = pacote.resumo_dict
    if not r:
        flash("Este pacote não tem resumo (foi importado antes desta função).", "erro")
        return redirect(url_for("imp.enviar"))
    categorias = {}
    for n in r["nao_criadas"]:
        categorias[n["categoria"]] = categorias.get(n["categoria"], 0) + 1
    return render_template("importar_resultado.html", pacote=pacote, r=r,
                           categorias=sorted(categorias.items(), key=lambda kv: -kv[1]))


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
    nao_criadas = []
    nomes_clientes = {c.id: c.nome for c in Cliente.query.all()}
    for i, p in enumerate(analise["propostas"]):
        if i not in destino:
            categoria, motivo = _motivo_desmarcada(p)
            nao_criadas.append({**_rotulo(p), "categoria": categoria, "motivo": motivo})
            continue
        for cliente_id in destino.get(i, []):
            if _ja_cadastrada(cliente_id, p):
                puladas.append(p["pasta"])
                nao_criadas.append({**_rotulo(p), "categoria": "Já cadastrada",
                                    "motivo": f"Já estava cadastrada para {nomes_clientes.get(cliente_id, 'o cliente')}."})
                continue
            try:
                lic = _criar_licitacao(cliente_id, p)
                db.session.commit()
                criadas.append(lic)
            except Exception as e:
                db.session.rollback()
                current_app.logger.exception("Falha ao importar %s", p["pasta"])
                erros.append(f"{p['pasta']}: {e}")
                nao_criadas.append({**_rotulo(p), "categoria": "Erro ao criar", "motivo": str(e)[:300]})

    pacote = _pacote_do_token(token, analise)
    shutil.rmtree(_pasta_token(token), ignore_errors=True)
    if pacote:
        ids = pacote.ids_licitacoes + [l.id for l in criadas]
        pacote.licitacoes_ids = ",".join(str(i) for i in ids)
        pacote.qtd_criadas = len(ids)
        pacote.situacao = "importado" if ids else pacote.situacao
        pacote.importado_em = datetime.utcnow()
        pacote.token = None
        pacote.resumo = json.dumps({
            "total": len(analise["propostas"]),
            "criadas": [{"id": l.id, "orgao": l.orgao_licitante, "numero": l.numero_pregao,
                         "cliente": l.cliente.nome if l.cliente else "",
                         "data": l.data_disputa.strftime("%Y-%m-%d") if l.data_disputa else ""} for l in criadas],
            "nao_criadas": nao_criadas,
        }, ensure_ascii=False)
        db.session.commit()
        return redirect(url_for("imp.resumo", id=pacote.id))

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
