"""Versao e status do sistema (bolinha ao lado do logo).

O auto-deploy da VPS (deploy/auto-deploy.sh) grava o estado em
/app/uploads/_deploy/status.json e cada deploy em historico.jsonl:
  estado: ok | build | falhou
"""
import json
import os
import time

PASTA = "/app/uploads/_deploy"

ROTULOS = {"ok": "Sistema no ar", "build": "Atualização em andamento",
           "falhou": "Última atualização falhou (versão anterior segue no ar)",
           "desconhecido": "Auto-deploy ainda não instalado"}


def status_sistema():
    caminho = os.path.join(PASTA, "status.json")
    try:
        with open(caminho, encoding="utf-8") as f:
            st = json.load(f)
    except (OSError, ValueError):
        st = {}
    estado = st.get("estado") if st.get("estado") in ("ok", "build", "falhou") else "desconhecido"
    # build parado ha mais de 30 min = interrompido (ex.: VPS reiniciou no meio)
    try:
        if estado == "build" and time.time() - os.path.getmtime(caminho) > 30 * 60:
            estado = "falhou"
            st["erro"] = "Atualização interrompida no meio. Rode o deploy de novo."
    except OSError:
        pass
    versao = st.get("versao")
    return {
        "estado": estado,
        "rotulo": ROTULOS[estado],
        "versao": f"v{versao}" if versao else "v—",
        "commit": st.get("commit") or "",
        "mensagem": st.get("mensagem") or "",
        "em": st.get("em") or "",
        "erro": st.get("erro") or "",
    }


def historico_deploys(limite=15):
    try:
        with open(os.path.join(PASTA, "historico.jsonl"), encoding="utf-8") as f:
            linhas = f.readlines()[-limite:]
    except OSError:
        return []
    saida = []
    for l in reversed(linhas):
        try:
            saida.append(json.loads(l))
        except ValueError:
            continue
    return saida
