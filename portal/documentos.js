// Página de documentos: lista os links de PDF servidos pela API de consulta.
//
// A API devolve documentos e linhas de planilha pelo mesmo endpoint, por isso
// toda requisição daqui fixa tema=documentos — a partição que o pipeline
// reserva aos links (ver core/pipeline.py, TEMA_DOCUMENTOS).

const TEMA_DOCUMENTOS = "documentos";
const POR_PAGINA = 20;

// Espelha TIPOS_DE_DOCUMENTO em core/pipeline.py: é o vocabulário fechado em
// que todo documento cai. Precisa existir aqui porque /api/v1/filtros varre
// todas as partições, inclusive as tabulares, e devolve centenas de tipos que
// nunca terão um PDF. Se a lista do Python mudar, esta precisa acompanhar.
//
// O valor é o nome da partição, sem acento; o rótulo é o que a pessoa lê.
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

// Endereço da API. Em produção o portal costuma ser servido pela mesma origem;
// no desenvolvimento a API sobe à parte, daí o localhost:8000 como padrão.
// Dá para apontar para outro host sem editar o arquivo: documentos.html?api=…
const API_BASE = (
  new URLSearchParams(location.search).get("api") ||
  window.PORTAL_API_BASE ||
  "http://localhost:8000"
).replace(/\/$/, "");

const state = {
  pagina: 1,
  totalPaginas: 0,
  total: 0,
};

const $ = (id) => document.getElementById(id);

// --------------------------------------------------------------------------
// Segurança: título, contexto e nome de arquivo vêm de páginas raspadas de
// terceiros. Nada disso entra no DOM sem passar por aqui.
// --------------------------------------------------------------------------

function escapar(valor) {
  if (valor === null || valor === undefined) return "";
  return String(valor)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

// Só http(s) viram link. Um `javascript:` que tivesse entrado na coleta
// executaria no clique, e o dado de origem não é confiável.
function urlSegura(valor) {
  if (!valor) return null;
  try {
    const url = new URL(String(valor), location.href);
    return url.protocol === "http:" || url.protocol === "https:"
      ? url.href
      : null;
  } catch {
    return null;
  }
}

// --------------------------------------------------------------------------
// Montagem das consultas
// --------------------------------------------------------------------------

function selecionados(id) {
  return [...$(id).selectedOptions].map((opcao) => opcao.value);
}

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

async function pedir(caminho, params) {
  const resposta = await fetch(`${API_BASE}${caminho}?${params}`);
  if (!resposta.ok) {
    throw new Error(`A API respondeu ${resposta.status} em ${caminho}.`);
  }
  return resposta.json();
}

// --------------------------------------------------------------------------
// Renderização
// --------------------------------------------------------------------------

function preencherSelect(id, valores) {
  $(id).innerHTML = valores
    .map((valor) => `<option value="${escapar(valor)}">${escapar(valor)}</option>`)
    .join("");
}

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

  // A busca da API casa contra tema, tipo_documento e uf — não contra o título.
  // Procurar "edital" devolve zero mesmo havendo dezenas de editais, e sem
  // dizer isso a pessoa conclui que o acervo é que está vazio.
  const buscando = $("filtro-busca").value.trim();
  const explicacao = buscando
    ? `<p class="empty-state">Nenhum documento encontrado para <strong>${escapar(buscando)}</strong>.
         <br>A busca da API cobre tema, tipo de documento e UF — ainda não o título.
         Para achar um documento pelo nome, filtre pelo tipo e percorra a lista.</p>`
    : `<p class="empty-state">Nenhum documento encontrado para esses filtros.</p>`;

  $("lista").innerHTML = explicacao;
}

function atualizarPaginacao() {
  const temPaginas = state.totalPaginas > 1;
  $("paginacao").hidden = !temPaginas;
  if (!temPaginas) return;

  $("paginacao-texto").textContent =
    `Página ${state.pagina.toLocaleString("pt-BR")} de ${state.totalPaginas.toLocaleString("pt-BR")}`;
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

/** Quantos documentos existem num tipo. Só o `total` interessa, daí page_size=1. */
async function contarPorTipo(tipo) {
  const params = new URLSearchParams({
    tema: TEMA_DOCUMENTOS,
    tipo_documento: tipo,
    page_size: 1,
  });
  const resposta = await pedir("/api/v1/documentos", params);
  return { tipo, total: resposta.total || 0 };
}

/** Preenche o filtro de tipo só com categorias que têm documento, e com a contagem. */
async function carregarTiposComContagem() {
  const contagens = await Promise.all(
    Object.keys(TIPOS_DE_DOCUMENTO).map(contarPorTipo)
  );
  const comDocumentos = contagens
    .filter((item) => item.total > 0)
    .sort((a, b) => b.total - a.total);

  $("filtro-tipo").innerHTML = comDocumentos
    .map(
      ({ tipo, total }) =>
        `<option value="${escapar(tipo)}">${escapar(rotulo(tipo))} (${total.toLocaleString("pt-BR")})</option>`
    )
    .join("");

  return comDocumentos.length;
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
  $("contador").textContent = `${state.total.toLocaleString("pt-BR")} ${plural}`;
  $("resumo-resultados").textContent = state.total
    ? `${state.total.toLocaleString("pt-BR")} ${plural} · clique em qualquer item para abrir o arquivo na fonte.`
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

function aoDigitar(callback, espera = 350) {
  let temporizador;
  return () => {
    clearTimeout(temporizador);
    temporizador = setTimeout(callback, espera);
  };
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
