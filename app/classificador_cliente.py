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
  valor estimado nesses itens;
- PALAVRAS DE EXCLUSAO: item com alguma delas em qualquer parte da
  descricao nao conta para o cliente (nem se tiver palavra de inclusao);
- GRUPO MISTO: quando os itens do cliente estao num grupo/lote junto com
  itens que nao tem nenhuma palavra-chave dele (ex.: meias + uniformes),
  ele nao pode disputar so a parte dele. Esses itens nao contam como
  "aproveitaveis". Cliente que so aparece em grupo misto nao e sugerido.
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


NOME_ITEM = 80  # caracteres do comeco da descricao que contam como "nome do produto"


def _nome_item(descricao):
    """Comeco da descricao do item (o nome do produto), normalizado. A palavra-chave
    tem que estar aqui: no meio da especificacao costuma ser so um detalhe
    (ex.: tinta "de acabamento uniforme" nao e uniforme)."""
    t = _normalizar(descricao)
    if len(t) <= NOME_ITEM:
        return t
    corte = t.rfind(" ", 0, NOME_ITEM + 1)
    return t[:corte if corte > 20 else NOME_ITEM]


def _chave_grupo(nome):
    """'Grupo 01', 'GRUPO 1' e 'grupo 1' viram a mesma chave."""
    txt = _normalizar(nome)
    return re.sub(r"\b0+(\d)", r"\1", txt)


def _campo(item, nome):
    return item.get(nome) if isinstance(item, dict) else getattr(item, nome, None)


def _excluido(excl, item_ou_texto):
    """True se a descricao (inteira) tem alguma palavra de exclusao."""
    if not excl:
        return False
    txt = item_ou_texto if isinstance(item_ou_texto, str) else _normalizar(
        f"{_campo(item_ou_texto, 'descricao') or ''}")
    return any(rx.search(txt) for rx in excl)


def padroes_exclusao(cliente):
    palavras = [p.palavra for p in (getattr(cliente, "palavras_exclusao", None) or [])] if cliente else []
    return [rx for rx in (_padrao(p) for p in palavras) if rx]


def grupos_mistos(padroes, itens, excl=None):
    """Grupos/lotes que tem item do cliente E item fora das palavras-chave dele.

    padroes: lista de regex do cliente. itens: dicts (importacao) ou
    ItemLicitacao. Devolve lista de
    {"grupo", "total", "do_cliente", "qtd_fora", "fora": [descricoes curtas], "chave"}
    """
    grupos = {}
    for item in itens or []:
        grupo = _campo(item, "lote_grupo")
        chave = _chave_grupo(grupo)
        if not chave:
            continue  # item sem grupo e disputado sozinho
        g = grupos.setdefault(chave, {"grupo": grupo, "itens": []})
        txt = _nome_item(_campo(item, "descricao"))
        g["itens"].append((item, any(rx.search(txt) for rx in padroes) and not _excluido(excl, item)))

    mistos = []
    for g in grupos.values():
        do_cliente = [i for i, ok in g["itens"] if ok]
        fora = [i for i, ok in g["itens"] if not ok]
        if do_cliente and fora:
            descr = []
            for i in fora[:3]:
                d = str(_campo(i, "descricao") or "").strip()
                descr.append(d[:60] + ("…" if len(d) > 60 else ""))
            mistos.append({"grupo": g["grupo"], "total": len(g["itens"]),
                           "do_cliente": len(do_cliente), "qtd_fora": len(fora),
                           "fora": descr,
                           "chave": _chave_grupo(g["grupo"])})
    mistos.sort(key=lambda m: m["grupo"] or "")
    return mistos


def _padroes_do_cliente(cliente):
    palavras = [p.palavra for p in (cliente.palavras_chave if cliente else [])]
    return [rx for rx in (_padrao(p) for p in palavras) if rx], palavras


class Classificador:
    """Monta uma vez os padroes de todos os clientes e classifica cada proposta."""

    def __init__(self, clientes):
        # clientes: lista de (id, nome, [palavras]) ou (id, nome, [palavras], [exclusoes])
        self.clientes = []
        self.exclusoes = {}
        for cid, nome, palavras, *resto in clientes:
            padroes = []
            for palavra in palavras:
                rx = _padrao(palavra)
                if rx:
                    padroes.append((palavra, rx))
            if padroes:
                self.clientes.append((cid, nome, padroes))
                self.exclusoes[cid] = [rx for rx in (_padrao(p) for p in (resto[0] if resto else [])) if rx]

    @property
    def vazio(self):
        return not self.clientes

    def classificar(self, itens, so_nome=True):
        """Devolve dict com o cliente sugerido e o ranking.

        {"cliente_id": 3 | None,
         "itens_total": 12,
         "ranking": [{"cliente_id", "nome", "itens", "valor", "palavras"}...],
         "empate": bool}
        so_nome: compara so o comeco da descricao (nome do item). False quando
        o "item" e o objeto inteiro da licitacao.
        """
        textos = []
        for item in itens or []:
            txt = _nome_item(item.get("descricao")) if so_nome else _normalizar(item.get("descricao"))
            if txt:
                valor = 0.0
                try:
                    valor = float(item.get("valor_estimado") or 0) * float(item.get("quantidade") or 1)
                except (TypeError, ValueError):
                    pass
                textos.append((txt, valor, _chave_grupo(item.get("lote_grupo")), _normalizar(item.get("descricao"))))

        ranking = []
        for cid, nome, padroes in self.clientes:
            n_itens, valor, achadas = 0, 0.0, []
            grupos_hit = []
            excl = self.exclusoes.get(cid)
            for txt, v, grupo, inteiro in textos:
                if _excluido(excl, inteiro):
                    continue
                hit = False
                for palavra, rx in padroes:
                    if rx.search(txt):
                        hit = True
                        if palavra not in achadas:
                            achadas.append(palavra)
                if hit:
                    n_itens += 1
                    valor += v
                    grupos_hit.append(grupo)
            if n_itens:
                mistos = grupos_mistos([rx for _, rx in padroes], itens, excl)
                chaves_mistas = {m["chave"] for m in mistos}
                aproveitaveis = sum(1 for g in grupos_hit if not g or g not in chaves_mistas)
                ranking.append({"cliente_id": cid, "nome": nome, "itens": n_itens,
                                "valor": round(valor, 2), "palavras": achadas[:6],
                                "aproveitaveis": aproveitaveis,
                                "so_grupo_misto": aproveitaveis == 0,
                                "mistos": [{k: m[k] for k in ("grupo", "total", "do_cliente", "qtd_fora", "fora")}
                                           for m in mistos]})

        # Quem so aparece em grupo misto vai para o fim: nao da para disputar
        ranking.sort(key=lambda r: (r["so_grupo_misto"], -r["itens"], -r["valor"], r["nome"]))
        validos = [r for r in ranking if not r["so_grupo_misto"]]
        empate = (len(validos) > 1 and validos[0]["itens"] == validos[1]["itens"]
                  and validos[0]["valor"] == validos[1]["valor"])
        return {
            "cliente_id": validos[0]["cliente_id"] if validos else None,
            "itens_total": len(textos),
            "ranking": ranking[:4],
            "empate": empate,
            "bloqueados": [r for r in ranking if r["so_grupo_misto"]],
        }

    def analisar_cliente(self, cliente_id, itens):
        """Grupos mistos de um cliente especifico (cliente escolhido no envio)."""
        for cid, _nome, padroes in self.clientes:
            if cid == cliente_id:
                return grupos_mistos([rx for _, rx in padroes], itens, self.exclusoes.get(cid))
        return []


def itens_do_cliente(cliente, itens):
    """Separa os itens que tem alguma palavra-chave do cliente.
    Devolve (visiveis, ocultos, palavras_cadastradas). Sem palavras-chave
    cadastradas, ou sem nenhum item que combine, todos ficam visiveis."""
    padroes, palavras = _padroes_do_cliente(cliente)
    if not padroes:
        return list(itens), [], palavras
    excl = padroes_exclusao(cliente)
    combina = []
    grupos_do_cliente = set()
    for item in itens:
        txt = _normalizar(f"{item.descricao or ''} {item.lote_grupo or ''}")
        ok = any(rx.search(txt) for rx in padroes) and not _excluido(excl, item)
        combina.append(ok)
        if ok and _chave_grupo(item.lote_grupo):
            grupos_do_cliente.add(_chave_grupo(item.lote_grupo))
    visiveis, ocultos = [], []
    for item, ok in zip(itens, combina):
        # Item de um grupo que tem item do cliente aparece junto: o grupo
        # e disputado inteiro, entao o cliente precisa ver tudo que esta nele
        if ok or (_chave_grupo(item.lote_grupo) in grupos_do_cliente):
            visiveis.append(item)
        else:
            ocultos.append(item)
    if not visiveis:
        return list(itens), [], palavras
    return visiveis, ocultos, palavras


def grupos_mistos_licitacao(cliente, itens):
    """Grupos mistos de uma licitacao ja cadastrada, para o aviso na tela.
    Devolve (mistos, ids_dos_itens_fora_do_ramo)."""
    padroes, _ = _padroes_do_cliente(cliente)
    if not padroes:
        return [], set()
    excl = padroes_exclusao(cliente)
    mistos = grupos_mistos(padroes, itens, excl)
    chaves = {m["chave"] for m in mistos}
    fora = set()
    for item in itens:
        if _chave_grupo(item.lote_grupo) in chaves:
            txt = _normalizar(item.descricao)
            if not any(rx.search(txt) for rx in padroes) or _excluido(excl, txt):
                fora.add(item.id)
    return mistos, fora
