"""Caixa de entrada do Bidfy: grava recados internos para um ou mais usuarios."""
from app import db
from app.models import MensagemCaixa


def enviar_mensagem(destinatarios, assunto, texto="", link=None, remetente_id=None, tipo="mensagem"):
    """destinatarios: lista de ids (ou de User). Nao faz commit (quem chama faz)."""
    vistos = set()
    for d in destinatarios:
        uid = getattr(d, "id", d)
        if not uid or uid in vistos or uid == remetente_id:
            continue
        vistos.add(uid)
        db.session.add(MensagemCaixa(destinatario_id=uid, remetente_id=remetente_id, tipo=tipo,
                                     assunto=(assunto or "")[:200], texto=texto or "",
                                     link=(link or None) and link[:300]))
    return len(vistos)


def nao_lidas(user_id):
    return MensagemCaixa.query.filter_by(destinatario_id=user_id, lida_em=None).count()
