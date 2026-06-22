// Modal editar item
document.querySelectorAll('.btn-editar-item').forEach(btn => {
  btn.addEventListener('click', () => {
    const id = btn.dataset.id;
    document.getElementById('edit-id').value = id;
    document.getElementById('edit-descricao').value = btn.dataset.descricao;
    document.getElementById('edit-marca').value = btn.dataset.marca;
    document.getElementById('edit-numero-item').value = btn.dataset.numeroItem;
    document.getElementById('edit-lote').value = btn.dataset.lote;
    document.getElementById('edit-qtd').value = btn.dataset.qtd;
    document.getElementById('edit-un').value = btn.dataset.un;
    document.getElementById('edit-valor').value = btn.dataset.valor;
    document.getElementById('form-editar-item').action = `/licitacoes/item/${id}/editar`;
    document.getElementById('modal-item').style.display = 'flex';
  });
});

const fechar = document.getElementById('fechar-modal');
if (fechar) {
  fechar.addEventListener('click', () => {
    document.getElementById('modal-item').style.display = 'none';
  });
}

// Fechar modal clicando fora
const modal = document.getElementById('modal-item');
if (modal) {
  modal.addEventListener('click', e => {
    if (e.target === modal) modal.style.display = 'none';
  });
}

// Mostrar nome dos arquivos selecionados
const inputFile = document.getElementById('input-file');
if (inputFile) {
  inputFile.addEventListener('change', () => {
    const label = document.querySelector('label[for="input-file"]');
    const n = inputFile.files.length;
    if (n > 0) label.textContent = `${n} arquivo(s) selecionado(s)`;
  });
}

// Mostrar campo de valor homologado ou motivo de encerramento conforme o status escolhido
const selectStatus = document.getElementById('select-status');
const campoValorHomologado = document.getElementById('campo-valor-homologado');
const campoMotivoEncerramento = document.getElementById('campo-motivo-encerramento');

function atualizarCamposCondicionaisStatus() {
  if (!selectStatus) return;
  const valor = selectStatus.value;
  if (campoValorHomologado) {
    campoValorHomologado.style.display = (valor === 'homologada') ? 'flex' : 'none';
  }
  if (campoMotivoEncerramento) {
    campoMotivoEncerramento.style.display = (valor === 'encerrada') ? 'flex' : 'none';
  }
}

if (selectStatus) {
  selectStatus.addEventListener('change', atualizarCamposCondicionaisStatus);
  atualizarCamposCondicionaisStatus(); // estado inicial, ao carregar a pagina
}

// Editar comentario inline (abre o form de edicao, esconde o texto)
document.querySelectorAll('.btn-editar-comentario').forEach(btn => {
  btn.addEventListener('click', () => {
    const id = btn.dataset.id;
    const texto = document.getElementById(`comentario-texto-${id}`);
    const form = document.getElementById(`form-editar-comentario-${id}`);
    if (texto) texto.style.display = 'none';
    if (form) form.style.display = 'flex';
  });
});

document.querySelectorAll('.btn-cancelar-edicao-comentario').forEach(btn => {
  btn.addEventListener('click', () => {
    const id = btn.dataset.id;
    const texto = document.getElementById(`comentario-texto-${id}`);
    const form = document.getElementById(`form-editar-comentario-${id}`);
    if (form) form.style.display = 'none';
    if (texto) texto.style.display = 'block';
  });
});
