"""
Identifica para qual cliente vai cada licitacao importada, comparando as
palavras-chave cadastradas de cada cliente com a DESCRICAO DOS ITENS do
edital (o objeto nao entra na conta: ele costuma ser generico demais).

Regras:
- texto comparado sem acento e sem diferenca de maiusculas;
- a palavra-chave precisa aparecer como palavra inteira (ou expressao
  inteira, quando tem mais de uma palavra);
- singular e plural contam igual ("uniforme" acha "uniformes",
  "cama hospitalar" acha "camas hospitalares");
- vence o cliente com mais itens encontrados; no empate, o que soma mais
  valor estimado nesses itens.
"""
import re
import unicodedata


def _normalizar(txt):
    txt = unicodedata.normalize("NFKD", str(txt or ""))
    txt = "".join(c for c in txt if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]+", " ", txt).strip()


def _raiz(palavra):
    """Tira o plural simples para que singular e plural casem."""
    if len(palavra) > 4 and palavra.endswith("oes"):
        return palavra[:-3], r"(?:ao|oes|aos|aes)"
    if len(palavra) > 4 and palavra.endswith("ao"):
        return palavra[:-2], r"(?:ao|oes|aos|aes)"
    if len(palavra) > 4 and palavra.endswith(("eis", "ais", "ois", "uis")):
        return palavra[:-2], r"(?:l|is)"
    if len(palavra) > 3 and palavra.endswith(("el", "al", "ol", "ul")):
        return palavra[:-1], r"(?:l|is)"
    if len(palavra) > 5 and palavra.endswith(("res", "zes", "les")):
        return palavra[:-2], r"(?:es)?"
    if len(palavra) > 3 and palavra.endswith("s") and not palavra.endswith("ss"):
        return palavra[:-1], r"(?:s|es)?"
    return palavra, r"(?:s|es)?"


def _padrao(palavra_chave):
    partes = _normalizar(palavra_chave).split()
    if not partes:
        return None
    pedacos = []
    for p in partes:
        if p.isdigit() or len(p) <= 2:
            pedacos.append(re.escape(p))
        else:
            raiz, sufixo = _raiz(p)
            pedacos.append(re.escape(raiz) + sufixo)
    return re.compile(r"\b" + r"\s+".join(pedacos) + r"\b")


class Classificador:
    """Monta uma vez os padroes de todos os clientes e classifica cada proposta."""

    def __init__(self, clientes):
        # clientes: lista de (id, nome, [palavras])
        self.clientes = []
        for cid, nome, palavras in clientes:
            padroes = []
            for palavra in palavras:
                rx = _padrao(palavra)
                if rx:
                    padroes.append((palavra, rx))
            if padroes:
                self.clientes.append((cid, nome, padroes))

    @property
    def vazio(self):
        return not self.clientes

    def classificar(self, itens):
        """Devolve dict com o cliente sugerido e o ranking.

        {"cliente_id": 3 | None,
         "itens_total": 12,
         "ranking": [{"cliente_id", "nome", "itens", "valor", "palavras"}...],
         "empate": bool}
        """
        textos = []
        for item in itens or []:
            txt = _normalizar(item.get("descricao"))
            if txt:
                valor = 0.0
                try:
                    valor = float(item.get("valor_estimado") or 0) * float(item.get("quantidade") or 1)
                except (TypeError, ValueError):
                    pass
                textos.append((txt, valor))

        ranking = []
        for cid, nome, padroes in self.clientes:
            n_itens, valor, achadas = 0, 0.0, []
            for txt, v in textos:
                hit = False
                for palavra, rx in padroes:
                    if rx.search(txt):
                        hit = True
                        if palavra not in achadas:
                            achadas.append(palavra)
                if hit:
                    n_itens += 1
                    valor += v
            if n_itens:
                ranking.append({"cliente_id": cid, "nome": nome, "itens": n_itens,
                                "valor": round(valor, 2), "palavras": achadas[:6]})

        ranking.sort(key=lambda r: (-r["itens"], -r["valor"], r["nome"]))
        empate = (len(ranking) > 1 and ranking[0]["itens"] == ranking[1]["itens"]
                  and ranking[0]["valor"] == ranking[1]["valor"])
        return {
            "cliente_id": ranking[0]["cliente_id"] if ranking else None,
            "itens_total": len(textos),
            "ranking": ranking[:4],
            "empate": empate,
        }


def itens_do_cliente(cliente, itens):
    """Separa os itens que tem alguma palavra-chave do cliente.
    Devolve (visiveis, ocultos, palavras_cadastradas). Sem palavras-chave
    cadastradas, ou sem nenhum item que combine, todos ficam visiveis."""
    palavras = [p.palavra for p in (cliente.palavras_chave if cliente else [])]
    padroes = [rx for rx in (_padrao(p) for p in palavras) if rx]
    if not padroes:
        return list(itens), [], palavras
    visiveis, ocultos = [], []
    for item in itens:
        txt = _normalizar(f"{item.descricao or ''} {item.lote_grupo or ''}")
        (visiveis if any(rx.search(txt) for rx in padroes) else ocultos).append(item)
    if not visiveis:
        return list(itens), [], palavras
    return visiveis, ocultos, palavras
