// Página de documentos: lista os links de PDF servidos pela API de consulta.
//
// A API devolve documentos e linhas de planilha pelo mesmo endpoint, por isso
// toda requisição daqui fixa tema=documentos — a partição que o pipeline
// reserva aos links (ver core/pipeline.py, TEMA_DOCUMENTOS).

import {
  $,
  API_BASE,
  aoDigitar,
  contagens,
  escapar,
  numero,
  pedir,
  preencherSelect,
  selecionados,
  urlSegura,
} from "./catalogo.js";

const TEMA_DOCUMENTOS = "documentos";
const POR_PAGINA = 20;

// Rótulos legíveis do vocabulário fechado de tipo (TIPOS_DE_DOCUMENTO, em
// core/pipeline.py). Quais tipos existem quem diz é a API, que já os devolve
// contados e restritos ao tema; o que falta é o acento e a maiúscula, que o
// nome da partição perdeu ao ser sanitizado. Um tipo fora desta lista ainda
// aparece — o `rotulo` improvisa a partir do próprio nome.
const TIPOS_DE_DOCUMENTO = {
  acordos: "Acordos",
  contratos: "Contratos",
  convenios: "Convênios",
  demonstracoes_contabeis: "Demonstrações contábeis",
  execucao_orcamentaria: "Execução orçamentária",
  licitacoes: "Licitações",
  pessoal: "Pessoal",
  processos_seletivos: "Processos seletivos",
  outros: "Outros",
};

const state = {
  pagina: 1,
  totalPaginas: 0,
  total: 0,
};

// --------------------------------------------------------------------------
// Montagem das consultas
// --------------------------------------------------------------------------

/** Parâmetros dos filtros, sem paginação — servem à lista e à exportação. */
function parametrosFiltro() {
  const params = new URLSearchParams({ tema: TEMA_DOCUMENTOS });

  const tipos = selecionados("filtro-tipo");
  if (tipos.length) params.set("tipo_documento", tipos.join(","));

  const ufs = selecionados("filtro-uf");
  if (ufs.length) params.set("uf", ufs.join(","));

  const anos = selecionados("filtro-ano");
  if (anos.length) params.set("ano", anos.join(","));

  const busca = $("filtro-busca").value.trim();
  if (busca) params.set("search", busca);

  return params;
}

// --------------------------------------------------------------------------
// Renderização
// --------------------------------------------------------------------------

/** Nome legível de um tipo. Os valores vêm sem acento, sanitizados do Parquet. */
function rotulo(valor) {
  return (
    TIPOS_DE_DOCUMENTO[valor] ||
    String(valor)
      .replaceAll("_", " ")
      .replace(/\b\w/g, (letra) => letra.toUpperCase())
  );
}

function itemDocumento(doc) {
  const href = urlSegura(doc.url_download);
  const titulo = escapar(doc.titulo || doc.nome_arquivo || "Documento sem título");
  const inativo = doc.ativo === "NÃO" || doc.ativo === "NAO" || doc.ativo === false;

  // Cada item precisa do próprio elemento: texto solto vira um único nó e o
  // `gap` do flex não o separa do vizinho.
  const tags = [];
  if (doc.uf) tags.push(`<span class="documento-tag">${escapar(doc.uf)}</span>`);
  if (doc.tipo_documento) {
    tags.push(`<span class="documento-tag">${escapar(rotulo(doc.tipo_documento))}</span>`);
  }
  if (doc.entidade) tags.push(`<span class="documento-tag">${escapar(doc.entidade)}</span>`);
  if (doc.publicado_em) {
    tags.push(`<span>Publicado em ${escapar(doc.publicado_em)}</span>`);
  }
  if (doc.secao_rota) tags.push(`<span>${escapar(doc.secao_rota)}</span>`);
  if (inativo) {
    tags.push(
      `<span class="documento-alerta">Link fora do ar na última verificação</span>`
    );
  }

  const arquivo = doc.nome_arquivo
    ? `<span class="documento-arquivo">${escapar(doc.nome_arquivo)}</span>`
    : "";

  const corpo = `
    <span class="documento-icone">${escapar(doc.tipo_arquivo || "DOC")}</span>
    <span class="documento-corpo">
      <span class="documento-titulo">${titulo}</span>
      <span class="documento-meta">${tags.join("")}</span>
      ${arquivo}
    </span>
    <span class="documento-seta" aria-hidden="true">↗</span>`;

  // Sem URL utilizável o item ainda aparece, mas não como link falso.
  if (!href) {
    return `<div class="documento-item inativo">${corpo}</div>`;
  }

  return `<a class="documento-item ${inativo ? "inativo" : ""}"
             href="${escapar(href)}" target="_blank" rel="noopener noreferrer">
            ${corpo}
          </a>`;
}

function renderizarLista(documentos) {
  if (documentos.length) {
    $("lista").innerHTML = documentos.map(itemDocumento).join("");
    return;
  }

  const buscando = $("filtro-busca").value.trim();
  const explicacao = buscando
    ? `<p class="empty-state">Nenhum documento encontrado para <strong>${escapar(buscando)}</strong>.
         <br>A busca cobre título, nome do arquivo, seção de origem, tipo e UF.</p>`
    : `<p class="empty-state">Nenhum documento encontrado para esses filtros.</p>`;

  $("lista").innerHTML = explicacao;
}

function atualizarPaginacao() {
  const temPaginas = state.totalPaginas > 1;
  $("paginacao").hidden = !temPaginas;
  if (!temPaginas) return;

  $("paginacao-texto").textContent =
    `Página ${numero(state.pagina)} de ${numero(state.totalPaginas)}`;
  $("pagina-anterior").disabled = state.pagina <= 1;
  $("pagina-proxima").disabled = state.pagina >= state.totalPaginas;
}

function atualizarLinksExportacao() {
  for (const formato of ["csv", "xlsx", "json"]) {
    const params = parametrosFiltro();
    params.set("formato", formato);
    $(`exportar-${formato}`).href =
      `${API_BASE}/api/v1/documentos/exportacao?${params}`;
  }
}

function mostrarErro(mensagem) {
  $("aviso-erro").hidden = false;
  $("aviso-erro-texto").textContent = `${mensagem} Confira se ela está no ar em ${API_BASE} e rode, na raiz do projeto:`;
  $("lista").innerHTML = "";
  $("resumo-resultados").textContent = "Sem conexão com a API.";
  $("contador").textContent = "—";
  $("paginacao").hidden = true;
}

// --------------------------------------------------------------------------
// Carregamento
// --------------------------------------------------------------------------

/** Preenche o filtro de tipo só com categorias que têm documento, e com a contagem.
 *
 * A contagem vem agrupada e já ordenada pela API; o vocabulário fechado do
 * TIPOS_DE_DOCUMENTO não entra aqui porque um tema só tem os tipos que tem —
 * o que a consulta devolve já é a lista certa.
 */
async function carregarTiposComContagem() {
  const porTipo = await contagens("tipo_documento", { tema: TEMA_DOCUMENTOS });

  $("filtro-tipo").innerHTML = [...porTipo]
    .map(
      ([tipo, total]) =>
        `<option value="${escapar(tipo)}">${escapar(rotulo(tipo))} (${numero(total)})</option>`
    )
    .join("");

  return porTipo.size;
}

async function carregarFiltros() {
  // UF e ano saem do /filtros direto: são vocabulários pequenos e previsíveis
  // (as 27 UFs mais o DN, poucos exercícios), então um valor sem documento é
  // apenas uma busca vazia, não ruído. O tipo é que precisa de tratamento.
  const filtros = await pedir("/api/v1/filtros", new URLSearchParams());
  preencherSelect("filtro-uf", filtros.ufs || []);
  preencherSelect("filtro-ano", filtros.anos || []);

  const tipos = await carregarTiposComContagem();
  $("badge-api").textContent = tipos ? "API conectada" : "API sem documentos";
  return tipos > 0;
}

async function carregarDocumentos() {
  $("lista").innerHTML = `<p class="carregando">Carregando documentos…</p>`;

  const params = parametrosFiltro();
  params.set("page", state.pagina);
  params.set("page_size", POR_PAGINA);

  const resposta = await pedir("/api/v1/documentos", params);

  state.total = resposta.total || 0;
  state.totalPaginas = resposta.total_pages || 0;

  renderizarLista(resposta.data || []);
  atualizarPaginacao();
  atualizarLinksExportacao();

  const plural = state.total === 1 ? "documento" : "documentos";
  $("contador").textContent = `${numero(state.total)} ${plural}`;
  $("resumo-resultados").textContent = state.total
    ? `${numero(state.total)} ${plural} · clique em qualquer item para abrir o arquivo na fonte.`
    : "Ajuste os filtros para encontrar documentos.";
}

/** Filtro novo recomeça da primeira página: a antiga pode nem existir mais. */
async function aplicarFiltros() {
  state.pagina = 1;
  await comErro(carregarDocumentos);
}

async function comErro(acao) {
  try {
    await acao();
    $("aviso-erro").hidden = true;
  } catch (erro) {
    mostrarErro(erro.message);
  }
}

async function irPara(pagina) {
  state.pagina = Math.min(Math.max(1, pagina), state.totalPaginas || 1);
  await comErro(carregarDocumentos);
  window.scrollTo({ top: 0, behavior: "smooth" });
}

async function main() {
  ["filtro-tipo", "filtro-uf", "filtro-ano"].forEach((id) =>
    $(id).addEventListener("change", aplicarFiltros)
  );
  $("filtro-busca").addEventListener("input", aoDigitar(aplicarFiltros));

  $("limpar").addEventListener("click", () => {
    $("filtro-busca").value = "";
    ["filtro-tipo", "filtro-uf", "filtro-ano"].forEach((id) => {
      [...$(id).options].forEach((opcao) => (opcao.selected = false));
    });
    aplicarFiltros();
  });

  $("pagina-anterior").addEventListener("click", () => irPara(state.pagina - 1));
  $("pagina-proxima").addEventListener("click", () => irPara(state.pagina + 1));

  await comErro(async () => {
    await carregarFiltros();
    await carregarDocumentos();
  });
}

main();
