"""
Preenche os Parametros da disputa de uma licitacao ja cadastrada com a lista
de itens do edital (PNCP, planilha ou tabela do TR). So acrescenta itens que
ainda nao existem e completa campos vazios; nada digitado e apagado.
"""
import os

from app import db
from app.models import Licitacao, ItemLicitacao
from app.itens_externos import buscar_itens


def preencher_itens(lic):
    """Devolve (novos, completados, fonte). fonte None = nao achou itens."""
    arquivos = [(d.nome_original or "", d.caminho) for d in lic.documentos
                if d.caminho and os.path.exists(d.caminho)]
    itens, fonte = buscar_itens(link=lic.link_edital or "", orgao=lic.orgao_licitante or "",
                                numero=lic.numero_pregao or "", uf=lic.uf, data=lic.data_disputa,
                                objeto=lic.objeto or "", municipio=lic.municipio, arquivos=arquivos)
    if not itens:
        return 0, 0, None
    existentes = {(i.numero_item or "").strip().lstrip("0"): i for i in lic.itens if (i.numero_item or "").strip()}
    novos, completados = 0, 0
    for it in itens:
        chave = (it["numero_item"] or "").strip().lstrip("0")
        atual = existentes.get(chave) if chave else None
        if atual is None:
            db.session.add(ItemLicitacao(licitacao_id=lic.id, numero_item=it["numero_item"],
                                         descricao=it["descricao"], lote_grupo=it["lote_grupo"],
                                         unidade=it["unidade"], quantidade=it["quantidade"],
                                         valor_estimado=it["valor_estimado"]))
            novos += 1
            continue
        mudou = False
        for campo in ("lote_grupo", "unidade", "quantidade", "valor_estimado"):
            if getattr(atual, campo) in (None, "") and it[campo] not in (None, ""):
                setattr(atual, campo, it[campo])
                mudou = True
        if len(it["descricao"]) > len(atual.descricao or "") + 20:
            atual.descricao = it["descricao"]
            mudou = True
        completados += mudou
    db.session.commit()
    return novos, completados, fonte


def preencher_todas(status=("agendada",)):
    """Roda o preenchimento em todas as licitacoes sem itens (uso pelo terminal)."""
    q = Licitacao.query.filter(~Licitacao.itens.any(), Licitacao.status.in_(status))
    achou, nao = 0, []
    for lic in q.order_by(Licitacao.data_disputa).all():
        try:
            novos, _c, fonte = preencher_itens(lic)
        except Exception as e:
            db.session.rollback()
            novos, fonte = 0, None
            print(f"#{lic.id} erro: {e}")
        if fonte:
            achou += 1
            print(f"#{lic.id} {lic.orgao_licitante[:45]} {lic.numero_pregao}: {novos} itens ({fonte[:60]})")
        else:
            nao.append(lic)
    print(f"\nPreenchidas: {achou}. Sem itens encontrados: {len(nao)}")
    for lic in nao:
        print(f"  #{lic.id} {lic.orgao_licitante[:50]} {lic.numero_pregao}")
