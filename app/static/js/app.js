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

// Areas de arrastar-e-soltar para os campos de documento (Edital / Termo de
// Referência / Outros) no card "Documentos" da licitação já criada — mesmo
// comportamento do "clicar e selecionar", só que também aceita soltar o arquivo.
document.querySelectorAll('.dropzone-doc').forEach((zona) => {
  const input = zona.querySelector('.input-file-doc');
  const label = zona.querySelector('.dropzone-doc-label');
  if (!input || !label) return;
  const textoOriginal = label.textContent;

  function atualizarLabel() {
    const n = input.files.length;
    if (n === 0) { label.textContent = textoOriginal; return; }
    label.textContent = n === 1 ? `📎 ${input.files[0].name}` : `📎 ${n} arquivos selecionados`;
  }
  input.addEventListener('change', atualizarLabel);

  ['dragenter', 'dragover'].forEach((evt) => {
    zona.addEventListener(evt, (e) => {
      e.preventDefault();
      e.stopPropagation();
      zona.classList.add('dropzone-doc-ativa');
    });
  });
  ['dragleave', 'drop'].forEach((evt) => {
    zona.addEventListener(evt, (e) => {
      e.preventDefault();
      e.stopPropagation();
      zona.classList.remove('dropzone-doc-ativa');
    });
  });
  zona.addEventListener('drop', (e) => {
    const arquivos = e.dataTransfer.files;
    if (arquivos && arquivos.length) {
      input.files = arquivos;
      atualizarLabel();
    }
  });
});

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

// Alterar status direto pelo painel — status que exigem justificativa (valor
// homologado / motivo de encerramento) abrem um card flutuante em vez de
// submeter na hora; os demais status sao salvos imediatamente.
(function () {
  const CAMPOS_JUSTIFICATIVA = {
    homologada: {
      inputOcultoClasse: 'input-valor-homologado-oculto',
      rotulo: 'Valor Total Homologado (R$) *',
      placeholder: '0,00',
    },
    encerrada: {
      inputOcultoClasse: 'input-motivo-encerramento-oculto',
      rotulo: 'Motivo do Encerramento *',
      placeholder: 'Ex: 2º colocado',
    },
  };

  function fecharCardJustificativa() {
    const existente = document.querySelector('.card-justificativa-flutuante');
    if (!existente) return;
    if (existente._onClickFora) document.removeEventListener('mousedown', existente._onClickFora);
    existente.remove();
  }

  function abrirCardJustificativa(select, form) {
    const config = CAMPOS_JUSTIFICATIVA[select.value];
    if (!config) return;
    fecharCardJustificativa();

    const inputOculto = form.querySelector('.' + config.inputOcultoClasse);
    const statusAnterior = select.dataset.statusAtual;

    const card = document.createElement('div');
    card.className = 'card-justificativa-flutuante';
    card.innerHTML =
      '<label>' + config.rotulo + '</label>' +
      '<input type="text" class="input-justificativa-flutuante" placeholder="' + config.placeholder + '">' +
      '<div class="card-justificativa-btns">' +
      '<button type="button" class="btn btn-sm btn-cancelar-justificativa">Cancelar</button>' +
      '<button type="button" class="btn btn-sm btn-primary btn-confirmar-justificativa">Confirmar</button>' +
      '</div>';
    document.body.appendChild(card);

    const inputCard = card.querySelector('.input-justificativa-flutuante');
    inputCard.value = inputOculto ? inputOculto.value : '';

    const rect = select.getBoundingClientRect();
    const larguraCard = 240;
    card.style.top = (rect.bottom + window.scrollY + 6) + 'px';
    card.style.left = Math.max(8, Math.min(rect.left + window.scrollX, window.innerWidth - larguraCard - 8)) + 'px';

    inputCard.focus();

    function cancelar() {
      select.value = statusAnterior;
      fecharCardJustificativa();
    }

    function confirmar() {
      const valorDigitado = inputCard.value.trim();
      if (!valorDigitado) {
        inputCard.style.borderColor = '#ef4444';
        inputCard.focus();
        return;
      }
      if (inputOculto) inputOculto.value = valorDigitado;
      select.dataset.statusAtual = select.value;
      fecharCardJustificativa();
      form.submit();
    }

    card.querySelector('.btn-cancelar-justificativa').addEventListener('click', cancelar);
    card.querySelector('.btn-confirmar-justificativa').addEventListener('click', confirmar);
    inputCard.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); confirmar(); }
      if (e.key === 'Escape') { e.preventDefault(); cancelar(); }
    });

    function onClickFora(e) {
      if (!card.contains(e.target) && e.target !== select) cancelar();
    }
    // so ativa o "fechar clicando fora" depois deste ciclo de eventos, senao
    // o proprio clique que abriu o card ja o fecharia
    setTimeout(() => document.addEventListener('mousedown', onClickFora), 0);
    card._onClickFora = onClickFora;
  }

  document.querySelectorAll('.select-status-mini').forEach((select) => {
    select.addEventListener('change', () => {
      const form = select.closest('form');
      if (!form) return;
      if (CAMPOS_JUSTIFICATIVA[select.value]) {
        abrirCardJustificativa(select, form);
      } else {
        form.submit();
      }
    });
  });
})();

// ─── Transições entre telas ────────────────────────────────────────────────
// O script faz a saída (rápida) e a entrada de cada tela, com direção no
// calendário (avançar/voltar) e troca suave entre mensal e semanal.
(function () {
  var CHAVE = 'bidfy-transicao';
  // A transicao nativa (@view-transition) ignorava cliques durante a animacao,
  // entao usamos sempre a versao feita aqui, que nunca bloqueia o clique.
  var nativo = false;
  var reduzir = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  function guardar(dir) { try { sessionStorage.setItem(CHAVE, dir || 'normal'); } catch (e) {} }
  function ler() {
    try { var d = sessionStorage.getItem(CHAVE); sessionStorage.removeItem(CHAVE); return d; } catch (e) { return null; }
  }

  document.addEventListener('click', function (e) {
    var a = e.target.closest && e.target.closest('a[href]');
    if (!a || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    if (a.target === '_blank' || a.hasAttribute('download')) return;
    var url = new URL(a.href, location.href);
    if (url.origin !== location.origin || url.hash && url.pathname === location.pathname && url.search === location.search) return;
    if (/\/(download|doc\/\d+\/download)/.test(url.pathname) || /\/download/.test(url.pathname)) return;
    guardar(a.dataset.transicao);
    if (!nativo && !reduzir) {
      var c = document.querySelector('main.container');
      if (c) {
        e.preventDefault();
        c.classList.add('tela-saindo');
        setTimeout(function () { location.href = a.href; }, 100);
      }
    }
  }, false);

  document.addEventListener('submit', function () { guardar('normal'); }, true);

  if (nativo) {
    window.addEventListener('pagereveal', function (e) {
      var dir = ler();
      if (e.viewTransition && (dir === 'avancar' || dir === 'voltar' || dir === 'visao')) {
        try { e.viewTransition.types.add(dir); } catch (err) {}
      }
    });
  } else if (!reduzir) {
    var dir = ler();
    var c = document.querySelector('main.container');
    if (c && dir) {
      c.classList.add(dir === 'avancar' ? 'tela-entrando-direita' : dir === 'voltar' ? 'tela-entrando-esquerda' : dir === 'visao' ? 'tela-entrando-visao' : 'tela-entrando');
    }
    // voltar pelo botão do navegador sem recarregar: limpa a animação de saída
    window.addEventListener('pageshow', function (ev) {
      if (ev.persisted && c) c.classList.remove('tela-saindo');
    });
  }
})();

// Arrastar e soltar em TODOS os campos de arquivo do sistema.
// Cada <input type="file"> que ainda nao esta numa area de arrastar ganha uma:
// clicar abre a escolha de arquivo; soltar o arquivo em cima preenche o campo.
(function () {
  function aceita(input, arquivo) {
    const accept = (input.getAttribute('accept') || '').trim();
    if (!accept) return true;
    const nome = arquivo.name.toLowerCase();
    return accept.split(',').some((a) => {
      a = a.trim().toLowerCase();
      if (!a) return false;
      if (a.startsWith('.')) return nome.endsWith(a);
      if (a.endsWith('/*')) return (arquivo.type || '').startsWith(a.slice(0, -1));
      return arquivo.type === a;
    });
  }

  function preencher(input, lista) {
    const validos = Array.from(lista).filter((f) => aceita(input, f));
    if (!validos.length) {
      alert('Este campo só aceita: ' + input.getAttribute('accept'));
      return false;
    }
    const dt = new DataTransfer();
    (input.multiple ? validos : validos.slice(0, 1)).forEach((f) => dt.items.add(f));
    input.files = dt.files;
    input.dispatchEvent(new Event('change', { bubbles: true }));
    return true;
  }

  document.querySelectorAll('input[type="file"]').forEach((input) => {
    if (input.closest('.dropzone-doc') || input.closest('.dropzone-geral')) return;
    const zona = document.createElement('div');
    zona.className = 'dropzone-geral';
    const texto = document.createElement('span');
    texto.className = 'dropzone-geral-texto';
    const padrao = input.multiple ? '📎 Selecionar ou arrastar arquivos' : '📎 Selecionar ou arrastar arquivo';
    texto.textContent = padrao;
    input.parentNode.insertBefore(zona, input);
    zona.appendChild(texto);
    zona.appendChild(input);
    input.classList.add('dropzone-geral-input');

    input.addEventListener('change', () => {
      const n = input.files.length;
      texto.textContent = n === 0 ? padrao : (n === 1 ? `📎 ${input.files[0].name}` : `📎 ${n} arquivos selecionados`);
      zona.classList.toggle('dropzone-geral-cheia', n > 0);
    });

    ['dragenter', 'dragover'].forEach((evt) => zona.addEventListener(evt, (e) => {
      e.preventDefault(); e.stopPropagation(); zona.classList.add('dropzone-geral-ativa');
    }));
    ['dragleave', 'drop'].forEach((evt) => zona.addEventListener(evt, (e) => {
      e.preventDefault(); e.stopPropagation(); zona.classList.remove('dropzone-geral-ativa');
    }));
    zona.addEventListener('drop', (e) => {
      if (e.dataTransfer && e.dataTransfer.files.length) preencher(input, e.dataTransfer.files);
    });
  });

  // Soltar o arquivo fora de um campo nao pode abrir o arquivo no navegador
  // (isso tiraria a pessoa da pagina e perderia o que ja foi preenchido).
  let contador = 0;
  window.addEventListener('dragenter', (e) => {
    if (e.dataTransfer && Array.from(e.dataTransfer.types || []).includes('Files')) {
      contador++; document.body.classList.add('arrastando-arquivo');
    }
  });
  window.addEventListener('dragleave', () => {
    contador = Math.max(0, contador - 1);
    if (!contador) document.body.classList.remove('arrastando-arquivo');
  });
  window.addEventListener('dragover', (e) => e.preventDefault());
  window.addEventListener('drop', (e) => {
    e.preventDefault(); contador = 0; document.body.classList.remove('arrastando-arquivo');
  });
})();

// Favoritar sem recarregar a pagina
document.querySelectorAll('.form-favorito').forEach((form) => {
  form.addEventListener('submit', (e) => {
    e.preventDefault();
    const btn = form.querySelector('.btn-favorito');
    fetch(form.action, { method: 'POST', headers: { 'X-Requested-With': 'fetch' }, credentials: 'same-origin' })
      .then((r) => r.ok ? r.json() : Promise.reject())
      .then((d) => {
        btn.classList.toggle('favorita', d.favorita);
        btn.setAttribute('aria-pressed', d.favorita ? 'true' : 'false');
        btn.title = d.favorita ? 'Tirar dos favoritos' : 'Favoritar';
        btn.classList.remove('favorito-pulso'); void btn.offsetWidth; btn.classList.add('favorito-pulso');
      })
      .catch(() => form.submit());
  });
});
