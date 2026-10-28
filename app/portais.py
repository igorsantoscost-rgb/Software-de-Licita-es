"""
Acesso dos clientes aos portais de disputa e a cobranca desses acessos.

A consultoria paga o acesso e repassa o valor na fatura mensal do cliente:
  - anual: entra uma vez, na proxima fatura em aberto, e vale ate a data informada;
  - mensal: entra em toda fatura mensal enquanto o acesso estiver valido;
  - por participacao: entra uma vez para cada licitacao do cliente naquele portal
    que chegou a ser disputada (status Em disputa em diante).

Cada cobranca lancada fica registrada em CobrancaPortal, entao rodar de novo
nunca cobra duas vezes (mesmo se o item for apagado da fatura depois).
"""
from datetime import date

from app import db
from app.capag import normalizar
from app.models import (AcessoPortal, CobrancaPortal, Cliente, Fatura, ItemFatura, Licitacao,
                        PORTAL_CHOICES, STATUS_PARTICIPOU)

MESES = ["", "janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
         "agosto", "setembro", "outubro", "novembro", "dezembro"]


def chave_portal(nome):
    return normalizar(nome)


def portais_disponiveis():
    """Todos os portais que ja apareceram no sistema (lista padrao, licitacoes
    cadastradas e acessos registrados), sem repetir."""
    nomes = [p for p in PORTAL_CHOICES if p != "Outro"]
    nomes += [p for (p,) in db.session.query(Licitacao.portal).distinct() if p]
    nomes += [p for (p,) in db.session.query(AcessoPortal.portal).distinct() if p]
    vistos, saida = set(), []
    for n in nomes:
        n = (n or "").strip()
        k = chave_portal(n)
        if k and k not in vistos:
            vistos.add(k)
            saida.append(n)
    return sorted(saida, key=chave_portal)


def acesso_ativo(cliente_id, portal, dia=None):
    """Acesso valido do cliente nesse portal (ou None)."""
    if not portal:
        return None
    k = chave_portal(portal)
    for a in AcessoPortal.query.filter_by(cliente_id=cliente_id).all():
        if chave_portal(a.portal) == k and a.ativo_em(dia or date.today()):
            return a
    return None


def _proximo_mes(m):
    return date(m.year + (m.month == 12), 1 if m.month == 12 else m.month + 1, 1)


def _aceita_item(fatura):
    return fatura.status == "aberta" and not fatura.enviada_em


def fatura_destino(cliente, mes):
    """Fatura que recebe a cobranca do mes: a do proprio mes, se estiver em
    aberto e nao enviada; senao a proxima em aberto. Nunca cria fatura de mes passado."""
    from app.financeiro_service import obter_ou_criar_fatura
    hoje = date.today().replace(day=1)
    mes = mes.replace(day=1)
    if mes < hoje:
        existente = Fatura.query.filter_by(cliente_id=cliente.id, mes_referencia=mes).first()
        if existente and _aceita_item(existente):
            return existente
        mes = hoje
    for _ in range(24):
        f = obter_ou_criar_fatura(cliente, mes)
        if _aceita_item(f):
            return f
        mes = _proximo_mes(mes)
    return None


def _brl(v):
    return "R$ " + f"{float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _lancar(acesso, chave, mes, descricao):
    if CobrancaPortal.query.filter_by(acesso_id=acesso.id, chave=chave).first():
        return False
    fatura = fatura_destino(acesso.cliente, mes)
    if not fatura:
        return False
    item = ItemFatura(fatura_id=fatura.id, acesso_portal_id=acesso.id,
                      descricao=descricao[:300], valor=acesso.valor)
    db.session.add(item)
    db.session.add(CobrancaPortal(acesso_id=acesso.id, chave=chave, fatura_id=fatura.id,
                                  valor=acesso.valor, descricao=descricao[:300]))
    db.session.flush()
    return True


def registrar_ja_cobrado(acesso):
    """Acesso que ja foi pago/cobrado antes de entrar no Bidfy: marca a cobranca
    atual como feita (anual: a unica; mensal: a do mes corrente)."""
    if not acesso.pago:
        return
    if acesso.forma_pagamento == "anual":
        chave = "anual"
    elif acesso.forma_pagamento == "mensal":
        chave = f"mensal:{date.today():%Y-%m}"
    else:
        return
    if not CobrancaPortal.query.filter_by(acesso_id=acesso.id, chave=chave).first():
        db.session.add(CobrancaPortal(acesso_id=acesso.id, chave=chave, fatura_id=None,
                                      valor=acesso.valor or 0, descricao="Já cobrado antes do cadastro no Bidfy"))


def lancar_cobrancas(cliente, hoje=None):
    """Lanca nas faturas o que falta cobrar dos acessos pagos do cliente.
    Devolve quantas cobrancas novas entraram."""
    hoje = hoje or date.today()
    novas = 0
    for a in AcessoPortal.query.filter_by(cliente_id=cliente.id).all():
        if not a.pago or not a.valor or float(a.valor) <= 0:
            continue
        if a.forma_pagamento == "anual":
            validade = f" (válido até {a.valido_ate:%d/%m/%Y})" if a.valido_ate else ""
            novas += _lancar(a, "anual", max(a.inicio, hoje), f"Acesso anual ao portal {a.portal}{validade}")
        elif a.forma_pagamento == "mensal":
            mes = a.inicio.replace(day=1)
            fim = hoje.replace(day=1)
            if a.valido_ate:
                fim = min(fim, a.valido_ate.replace(day=1))
            while mes <= fim:
                novas += _lancar(a, f"mensal:{mes:%Y-%m}", mes,
                                 f"Acesso mensal ao portal {a.portal} — {MESES[mes.month]}/{mes.year}")
                mes = _proximo_mes(mes)
        elif a.forma_pagamento == "participacao":
            k = chave_portal(a.portal)
            lics = Licitacao.query.filter(Licitacao.cliente_id == cliente.id,
                                          Licitacao.status.in_(STATUS_PARTICIPOU),
                                          Licitacao.data_disputa.isnot(None)).all()
            for lic in lics:
                dia = lic.data_disputa.date()
                if chave_portal(lic.portal) != k or not a.ativo_em(dia) or dia > hoje:
                    continue
                novas += _lancar(a, f"lic:{lic.id}", dia,
                                 f"Participação no portal {a.portal} — {lic.orgao_licitante} "
                                 f"(Pregão {lic.numero_pregao}, {dia:%d/%m/%Y})")
    return novas


def lancar_cobrancas_todos():
    """Rodada diaria (lembrete_diario): todos os clientes com acesso pago."""
    total = 0
    ids = {cid for (cid,) in db.session.query(AcessoPortal.cliente_id)
           .filter(AcessoPortal.pago.is_(True)).distinct()}
    for c in Cliente.query.filter(Cliente.id.in_(ids)).all() if ids else []:
        try:
            total += lancar_cobrancas(c)
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            print(f"Acessos a portais: falhou para {c.nome} ({e})")
    return total


def valor_reais(txt):
    """'1.200,00' / '1200' / 'R$ 99,90' -> Decimal; vazio -> None."""
    from decimal import Decimal, InvalidOperation
    t = (txt or "").strip().lower().replace("r$", "").replace(" ", "")
    if not t:
        return None
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    elif t.count(".") == 1 and len(t.split(".")[1]) == 3:
        t = t.replace(".", "")
    try:
        v = Decimal(t).quantize(Decimal("0.01"))
    except InvalidOperation:
        raise ValueError(txt)
    return v if v > 0 else None
