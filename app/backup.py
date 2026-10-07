"""
Backup do Bidfy.

- Backup diario: um unico arquivo (/app/uploads/_backup/backup_diario.zip)
  com todos os dados do banco, sobrescrito uma vez por dia. Ele e gerado na
  primeira visita ao sistema de cada dia (e tambem pelo script diario de
  lembretes, se ele estiver agendado).
- Download completo: um .zip montado na hora com os dados do banco e todos
  os arquivos enviados (editais, documentos, boletos...).

Os dados vao em JSON, uma lista de linhas por tabela, mais um manifesto.
"""
import fcntl
import io
import json
import os
import threading
import zipfile
from datetime import date, datetime
from decimal import Decimal

from app import db

PASTA_UPLOADS = "/app/uploads"
PASTA_BACKUP = os.path.join(PASTA_UPLOADS, "_backup")
ARQUIVO_DIARIO = os.path.join(PASTA_BACKUP, "backup_diario.zip")
# Pastas que nao entram no download completo (temporarias ou o proprio backup)
PASTAS_FORA = {"_backup", "_importacoes"}


def _valor_json(v):
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, (bytes, bytearray, memoryview)):
        return None
    return v


def _dados_do_banco():
    """{tabela: [linhas]} de todas as tabelas do sistema."""
    import app.models  # noqa: F401  (garante que todas as tabelas estao no metadata)
    tabelas = {}
    with db.engine.connect() as conn:
        for tabela in db.metadata.sorted_tables:
            if tabela.name == "lixeira":
                # a lixeira guarda dados em formato binario; registra so o resumo
                colunas = [c for c in tabela.c if c.name not in ("dados", "arquivos")]
                linhas = conn.execute(tabela.select().with_only_columns(*colunas)).mappings().all()
            else:
                linhas = conn.execute(tabela.select()).mappings().all()
            tabelas[tabela.name] = [{k: _valor_json(v) for k, v in l.items()} for l in linhas]
    return tabelas


def escrever_dados(zf):
    tabelas = _dados_do_banco()
    manifesto = {
        "sistema": "Bidfy",
        "gerado_em": datetime.now().isoformat(timespec="seconds"),
        "tabelas": {nome: len(linhas) for nome, linhas in tabelas.items()},
    }
    zf.writestr("LEIA-ME.txt",
                "Backup do Bidfy\n"
                f"Gerado em {datetime.now().strftime('%d/%m/%Y %H:%M')}\n\n"
                "dados/<tabela>.json: todas as linhas de cada tabela do sistema.\n"
                "arquivos/: os arquivos enviados (so no backup completo).\n")
    zf.writestr("manifesto.json", json.dumps(manifesto, ensure_ascii=False, indent=2))
    for nome, linhas in tabelas.items():
        zf.writestr(f"dados/{nome}.json", json.dumps(linhas, ensure_ascii=False, indent=1))
    return manifesto


def gerar_backup_diario():
    """Gera (sobrescrevendo) o backup diario. Seguro com varios processos."""
    os.makedirs(PASTA_BACKUP, exist_ok=True)
    trava = open(os.path.join(PASTA_BACKUP, ".trava"), "w")
    try:
        fcntl.flock(trava, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        trava.close()
        return False  # outro processo ja esta gerando
    try:
        temporario = ARQUIVO_DIARIO + ".tmp"
        with zipfile.ZipFile(temporario, "w", zipfile.ZIP_DEFLATED) as zf:
            escrever_dados(zf)
        os.replace(temporario, ARQUIVO_DIARIO)  # troca de uma vez (nunca fica pela metade)
        return True
    finally:
        fcntl.flock(trava, fcntl.LOCK_UN)
        trava.close()


def info_backup_diario():
    if not os.path.exists(ARQUIVO_DIARIO):
        return None
    st = os.stat(ARQUIVO_DIARIO)
    return {"gerado_em": datetime.fromtimestamp(st.st_mtime), "tamanho": st.st_size}


def backup_de_hoje_feito():
    info = info_backup_diario()
    return bool(info and info["gerado_em"].date() == date.today())


_em_andamento = threading.Lock()


def garantir_backup_do_dia(app):
    """Chamado nas visitas: se o backup de hoje ainda nao existe, gera em
    segundo plano (a pessoa nao espera por ele)."""
    if backup_de_hoje_feito() or not _em_andamento.acquire(blocking=False):
        return

    def tarefa():
        try:
            with app.app_context():
                from app import lixeira
                gerar_backup_diario()
                lixeira.limpar_vencidos()
        except Exception:
            app.logger.exception("Falha no backup diario")
        finally:
            _em_andamento.release()

    threading.Thread(target=tarefa, daemon=True).start()


# ─── Download completo (dados + arquivos), montado aos poucos ────────────────

class _Saida(io.RawIOBase):
    """Recebe o que o zipfile escreve e entrega em pedacos para a resposta."""

    def __init__(self):
        self.pedacos = []
        self.posicao = 0

    def writable(self):
        return True

    def write(self, b):
        self.pedacos.append(bytes(b))
        self.posicao += len(b)
        return len(b)

    def tell(self):
        return self.posicao

    def retirar(self):
        dados = b"".join(self.pedacos)
        self.pedacos = []
        return dados


def gerar_backup_completo():
    """Gerador de bytes de um .zip com dados + arquivos (sem montar no disco)."""
    saida = _Saida()
    with zipfile.ZipFile(saida, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
        escrever_dados(zf)
        yield saida.retirar()
        for raiz, pastas, arquivos in os.walk(PASTA_UPLOADS):
            rel = os.path.relpath(raiz, PASTA_UPLOADS)
            if rel == ".":
                pastas[:] = [p for p in pastas if p not in PASTAS_FORA]
            for nome in arquivos:
                caminho = os.path.join(raiz, nome)
                destino = os.path.join("arquivos", os.path.relpath(caminho, PASTA_UPLOADS))
                try:
                    with open(caminho, "rb") as origem, zf.open(destino, "w", force_zip64=True) as alvo:
                        while True:
                            bloco = origem.read(1024 * 1024)
                            if not bloco:
                                break
                            alvo.write(bloco)
                            if saida.posicao and len(saida.pedacos) > 4:
                                yield saida.retirar()
                except OSError:
                    continue
                yield saida.retirar()
    yield saida.retirar()
