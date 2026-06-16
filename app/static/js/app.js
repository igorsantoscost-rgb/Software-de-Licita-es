// Modal editar item
document.querySelectorAll('.btn-editar-item').forEach(btn => {
  btn.addEventListener('click', () => {
    const id = btn.dataset.id;
    document.getElementById('edit-id').value = id;
    document.getElementById('edit-descricao').value = btn.dataset.descricao;
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
