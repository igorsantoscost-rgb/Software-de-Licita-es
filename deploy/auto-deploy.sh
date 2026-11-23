#!/usr/bin/env bash
# Auto-deploy do Bidfy. Roda a cada minuto (systemd timer, ver instalar-auto-deploy.sh).
# Se o GitHub tiver commit novo na main: git pull + docker compose up -d --build.
# Grava o estado em uploads/_deploy/status.json (bolinha ao lado do logo):
#   build = atualizando | ok = no ar | falhou = deu erro (a versao anterior segue no ar)
# Uso manual: bash deploy/auto-deploy.sh --forcar   (refaz o build mesmo sem commit novo)
set -u
DIR="$(cd "$(dirname "$0")/.." && pwd)"
RAMO="main"
STATUS_DIR="$DIR/uploads/_deploy"
LOG="$STATUS_DIR/ultimo-deploy.log"
URL_TESTE="http://127.0.0.1:8092/login"

exec 9>/tmp/bidfy-auto-deploy.lock
flock -n 9 || exit 0            # ja tem um deploy rodando

cd "$DIR" || exit 1
mkdir -p "$STATUS_DIR"

git fetch -q origin "$RAMO" 2>>"$STATUS_DIR/fetch-erros.log" || exit 0
LOCAL="$(git rev-parse HEAD)"
REMOTO="$(git rev-parse "origin/$RAMO")"
if [ "$LOCAL" = "$REMOTO" ] && [ "${1:-}" != "--forcar" ] && [ -f "$STATUS_DIR/status.json" ]; then
  exit 0
fi

# estado versao commit erro  -> status.json (+ historico.jsonl quando nao for "build")
gravar() {
  ESTADO="$1" VERSAO="$2" COMMIT="$3" ERRO="${4:-}" STATUS_DIR="$STATUS_DIR" \
  MENSAGEM="$(git log -1 --format=%s "$3" 2>/dev/null)" python3 - <<'PY'
import json, os, datetime, zoneinfo
agora = datetime.datetime.now(zoneinfo.ZoneInfo("America/Sao_Paulo")).strftime("%d/%m/%Y %H:%M")
d = {"estado": os.environ["ESTADO"], "versao": os.environ["VERSAO"], "commit": os.environ["COMMIT"],
     "mensagem": os.environ.get("MENSAGEM", ""), "erro": os.environ.get("ERRO", "")[-1500:], "em": agora}
pasta = os.environ["STATUS_DIR"]
tmp = os.path.join(pasta, "status.json.tmp")
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(d, f, ensure_ascii=False)
os.replace(tmp, os.path.join(pasta, "status.json"))
if d["estado"] != "build":
    with open(os.path.join(pasta, "historico.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(d, ensure_ascii=False) + "\n")
PY
}

VERSAO_NOVA="$(git rev-list --count "origin/$RAMO" 2>/dev/null || echo '?')"
gravar build "$VERSAO_NOVA" "$REMOTO"

{
  echo "=== $(date '+%d/%m/%Y %H:%M:%S') deploy de ${REMOTO:0:7} ==="
  git pull --ff-only origin "$RAMO" && docker compose up -d --build
} >"$LOG" 2>&1
RC=$?

if [ $RC -ne 0 ]; then
  gravar falhou "$VERSAO_NOVA" "$REMOTO" "$(tail -n 25 "$LOG")"
  exit 1
fi

# espera o app responder (ate ~90s)
for _ in $(seq 1 30); do
  CODIGO="$(curl -s -o /dev/null -w '%{http_code}' "$URL_TESTE" || true)"
  if [ "$CODIGO" = "200" ] || [ "$CODIGO" = "302" ]; then
    docker image prune -f >/dev/null 2>&1 || true
    gravar ok "$(git rev-list --count HEAD)" "$(git rev-parse HEAD)"
    exit 0
  fi
  sleep 3
done
docker compose logs app --tail=40 >>"$LOG" 2>&1
gravar falhou "$VERSAO_NOVA" "$REMOTO" "O sistema nao respondeu depois do build. $(tail -n 20 "$LOG")"
exit 1
