"""
Lixeira: o que e excluido no Bidfy fica guardado por 30 dias e pode ser
restaurado (tela visivel so para o Igor).

Duas lixeiras:
- "licitacoes": licitacoes excluidas (com itens, documentos, comentarios,
  observacoes e favoritos);
- "uploads": arquivos excluidos ou substituidos (documentos da licitacao,
  documentos de apoio, documentos do cliente, documentos de empenho e
  pacotes de editais importados).

Ao excluir, as linhas do banco sao copiadas para a lixeira e os arquivos sao
movidos para /app/uploads/_lixeira/<id>/. Restaurar recoloca as linhas (com
os mesmos ids) e devolve os arquivos para o lugar de origem.
"""
import os
import shutil
import uuid
from datetime import datetime, timedelta

from sqlalchemy import inspect as sa_inspect
from sqlalchemy.exc import IntegrityError

from app import db

DIAS_NA_LIXEIRA = 30
PASTA_LIXEIRA = os.path.join("/app/uploads", "_lixeira")

ROTULOS_TIPO = {
    "licitacao": "Licitação",
    "documento": "Documento da licitação",
    "documento_apoio": "Documento de apoio",
    "documento_cliente": "Documento do cliente",
    "documento_empenho": "Documento de empenho",
    "pacote": "Pacote de editais",
}


def _linha(obj):
    """(nome_da_tabela, {coluna: valor}) de um objeto do banco."""
    mapper = sa_inspect(obj).mapper
    dados = {}
    for attr in mapper.column_attrs:
        dados[attr.columns[0].name] = getattr(obj, attr.key)
    return (mapper.local_table.name, dados)


def _guardar_arquivos(caminhos, pasta_item):
    """Move os arquivos para a pasta do item na lixeira."""
    guardados, total = [], 0
    for i, caminho in enumerate(c for c in caminhos if c):
        if not os.path.exists(caminho):
            continue
        os.makedirs(pasta_item, exist_ok=True)
        destino = os.path.join(pasta_item, f"{i}_{os.path.basename(caminho)}")
        total += os.path.getsize(caminho)
        shutil.move(caminho, destino)
        guardados.append((caminho, destino))
    return guardados, total


def enviar(objetos, lixeira, tipo, titulo, detalhe=None, arquivos=None, usuario_id=None):
    """Copia as linhas para a lixeira e move os arquivos. NAO apaga as linhas
    do banco nem faz commit: quem chama continua fazendo o db.session.delete
    de sempre e o commit (assim tudo acontece numa transacao so)."""
    from app.models import ItemLixeira
    linhas = [_linha(o) for o in objetos]
    pasta_item = os.path.join(PASTA_LIXEIRA, uuid.uuid4().hex)
    guardados, total = _guardar_arquivos(arquivos or [], pasta_item)
    item = ItemLixeira(
        lixeira=lixeira, tipo=tipo, titulo=(titulo or "")[:400], detalhe=(detalhe or "")[:500] or None,
        dados=linhas, arquivos=guardados, tamanho=total, excluido_por=usuario_id,
        excluido_em=datetime.utcnow(),
    )
    db.session.add(item)
    return item


def enviar_licitacao(lic, usuario_id=None, motivo=None):
    """Snapshot completo da licitacao (com tudo o que pertence a ela)."""
    objetos = [lic] + list(lic.itens) + list(lic.documentos) + list(lic.comentarios) \
        + list(lic.observacoes_apoio) + list(lic.favoritos)
    detalhe = f"Pregão {lic.numero_pregao} · {lic.cliente.nome if lic.cliente else ''}"
    if lic.data_disputa:
        detalhe += f" · disputa {lic.data_disputa.strftime('%d/%m/%Y')}"
    if motivo:
        detalhe += f" · {motivo}"
    return enviar(objetos, "licitacoes", "licitacao", lic.orgao_licitante, detalhe,
                  arquivos=[d.caminho for d in lic.documentos], usuario_id=usuario_id)


def enviar_documento(doc, tipo, titulo_extra="", usuario_id=None):
    """Um arquivo (Documento, DocumentoCliente ou DocumentoEmpenho)."""
    return enviar([doc], "uploads", tipo, doc.nome_original, titulo_extra,
                  arquivos=[doc.caminho], usuario_id=usuario_id)


# ─── Restaurar ───────────────────────────────────────────────────────────────

def _tabela(nome):
    return db.metadata.tables[nome]


def restaurar(item):
    """Recoloca as linhas e os arquivos. Retorna (ok, mensagem)."""
    linhas = item.dados or []
    # Confere conflitos antes de mexer em qualquer coisa
    for nome, dados in linhas:
        tabela = _tabela(nome)
        pk = [c.name for c in tabela.primary_key.columns]
        filtro = [tabela.c[c] == dados[c] for c in pk]
        if db.session.execute(tabela.select().where(*filtro)).first():
            return False, "Já existe um registro com o mesmo número no sistema; este item não pode ser restaurado."
    pais = {
        "licitacoes": ("clientes", "cliente_id", "O cliente desta licitação foi removido."),
        "documentos": ("licitacoes", "licitacao_id", "A licitação deste arquivo não existe mais. Restaure a licitação primeiro (lixeira de licitações)."),
        "documentos_cliente": ("clientes", "cliente_id", "O cliente deste documento não existe mais."),
        "documentos_empenho": ("empenhos", "empenho_id", "O empenho deste documento não existe mais."),
    }
    nome0, dados0 = linhas[0]
    if nome0 in pais:
        tabela_pai, coluna, msg = pais[nome0]
        tp = _tabela(tabela_pai)
        if not db.session.execute(tp.select().where(tp.c.id == dados0[coluna])).first():
            return False, msg

    try:
        for nome, dados in linhas:
            db.session.execute(_tabela(nome).insert().values(**dados))
        db.session.flush()
    except IntegrityError as e:
        db.session.rollback()
        return False, f"Não foi possível restaurar: {e.orig}"

    for original, guardado in item.arquivos or []:
        if os.path.exists(guardado):
            os.makedirs(os.path.dirname(original), exist_ok=True)
            shutil.move(guardado, original)
    pasta = _pasta_do_item(item)
    db.session.delete(item)
    db.session.commit()
    if pasta:
        shutil.rmtree(pasta, ignore_errors=True)
    return True, f"Restaurado: {ROTULOS_TIPO.get(item.tipo, 'item').lower()} \"{item.titulo}\". Já está de volta no sistema."


def _pasta_do_item(item):
    for _orig, guardado in item.arquivos or []:
        return os.path.dirname(guardado)
    return None


def apagar_definitivo(item):
    pasta = _pasta_do_item(item)
    db.session.delete(item)
    db.session.commit()
    if pasta:
        shutil.rmtree(pasta, ignore_errors=True)


def limpar_vencidos():
    """Apaga de vez o que esta na lixeira ha mais de 30 dias."""
    from app.models import ItemLixeira
    limite = datetime.utcnow() - timedelta(days=DIAS_NA_LIXEIRA)
    vencidos = ItemLixeira.query.filter(ItemLixeira.excluido_em < limite).all()
    for item in vencidos:
        pasta = _pasta_do_item(item)
        db.session.delete(item)
        if pasta:
            shutil.rmtree(pasta, ignore_errors=True)
    if vencidos:
        db.session.commit()
    return len(vencidos)


def dias_restantes(item):
    fim = item.excluido_em + timedelta(days=DIAS_NA_LIXEIRA)
    return max(0, (fim - datetime.utcnow()).days)
