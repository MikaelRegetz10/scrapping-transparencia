// Página de planilhas: o catálogo dos arquivos tabulares coletados.
//
// Toda requisição daqui fixa tema=planilhas — a partição que o pipeline
// reserva ao inventário desses links (ver core/pipeline.py, TEMA_PLANILHAS).
// É o inventário, não o conteúdo: as linhas de dentro de cada planilha vivem
// nos temas próprios delas ("dados_abertos", "administracao_regional_…").
//
// A diferença para a página de documentos está no que cada item carrega. Um
// PDF só precisa abrir; uma planilha passou por um profiler, e o que ele
// achou — se o arquivo abriu, se as colunas fazem sentido — é metade da
// informação útil sobre ela.

import {
  $,
  API_BASE,
  aoDigitar,
  contagens,
  escapar,
  numero,
  pedir,
  selecionados,
  urlSegura,
} from "./catalogo.js";

const TEMA_PLANILHAS = "planilhas";
const POR_PAGINA = 20;

// Rótulos legíveis do vocabulário fechado de tipo, igual à página de
// documentos: os dois catálogos partilham o mesmo vocabulário
// (TIPOS_DE_DOCUMENTO, em core/pipeline.py). Quais tipos existem quem diz é a
// API; daqui sai só o acento e a maiúscula que a sanitização levou.
const TIPOS_DE_DADO = {
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

// Formatos que o core/profiler.py sabe abrir (PROFILABLE_TYPES, em
// core/pipeline.py). Os demais entram no catálogo auditados só quanto à
// disponibilidade — e o item precisa dizer isso, senão "conteúdo não lido"
// parece falha da coleta quando é só um .zip fazendo o que .zip faz.
const FORMATOS_TABULARES = new Set(["csv", "xlsx", "xls", "json"]);

// Os cartões do topo. Cada um lê um número já apurado pelas agregações que
// montam os filtros — nenhum custa consulta própria — e é também um atalho:
// clicar aplica exatamente os filtros que produziram aquele número.
const CARTOES = [
  {
    id: "total",
    rotulo: "Arquivos catalogados",
    detalhe: "Todos os links tabulares auditados",
    filtros: {},
  },
  {
    id: "csv",
    rotulo: "Em CSV",
    detalhe: "O formato aberto por excelência",
    filtros: { tipo_arquivo: "csv" },
  },
  {
    id: "lidos",
    rotulo: "Conteúdo no acervo",
    detalhe: "O profiler leu e as linhas foram para o Parquet",
    filtros: { estruturado: "SIM" },
  },
  {
    id: "quebrados",
    rotulo: "Links fora do ar",
    detalhe: "Não responderam na última verificação",
    filtros: { ativo: "NÃO" },
    alerta: true,
  },
];

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
  const params = new URLSearchParams({ tema: TEMA_PLANILHAS });

  const formatos = selecionados("filtro-formato");
  if (formatos.length) params.set("tipo_arquivo", formatos.join(","));

  const tipos = selecionados("filtro-tipo");
  if (tipos.length) params.set("tipo_documento", tipos.join(","));

  const entidades = selecionados("filtro-entidade");
  if (entidades.length) params.set("entidade", entidades.join(","));

  const situacao = $("filtro-situacao").value;
  if (situacao) params.set("ativo", situacao);

  const leitura = $("filtro-leitura").value;
  if (leitura) params.set("estruturado", leitura);

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
    TIPOS_DE_DADO[valor] ||
    String(valor)
      .replaceAll("_", " ")
      .replace(/\b\w/g, (letra) => letra.toUpperCase())
  );
}

/** "1,2 MB" ou "79,8 kB" — o Parquet guarda sempre em kB. */
function tamanho(kb) {
  const valor = Number(kb);
  if (!Number.isFinite(valor) || valor <= 0) return "";
  return valor >= 1024
    ? `${(valor / 1024).toLocaleString("pt-BR", { maximumFractionDigits: 1 })} MB`
    : `${valor.toLocaleString("pt-BR", { maximumFractionDigits: 1 })} kB`;
}

function ehSim(valor) {
  return valor === "SIM" || valor === true;
}

/** O selo de leitura do conteúdo, que só faz sentido para formato tabular. */
function seloLeitura(planilha) {
  const formato = String(planilha.tipo_arquivo || "").toLowerCase();

  if (ehSim(planilha.estruturado)) {
    return `<span class="planilha-selo lido">Conteúdo no acervo</span>`;
  }
  if (!FORMATOS_TABULARES.has(formato)) {
    return `<span class="planilha-selo neutro">Formato não tabular</span>`;
  }
  return `<span class="planilha-selo nao-lido">Conteúdo não lido</span>`;
}

function itemPlanilha(planilha) {
  const href = urlSegura(planilha.url_download);
  const titulo = escapar(
    planilha.titulo || planilha.nome_arquivo || "Arquivo sem título"
  );
  const inativo = !ehSim(planilha.ativo);
  const formato = String(planilha.tipo_arquivo || "arq").toUpperCase();

  // Cada item precisa do próprio elemento: texto solto vira um único nó e o
  // `gap` do flex não o separa do vizinho.
  const tags = [];
  if (planilha.tipo_documento) {
    tags.push(
      `<span class="documento-tag">${escapar(rotulo(planilha.tipo_documento))}</span>`
    );
  }
  if (planilha.entidade) {
    tags.push(`<span class="documento-tag">${escapar(planilha.entidade)}</span>`);
  }
  tags.push(seloLeitura(planilha));

  const peso = tamanho(planilha.tamanho_kb);
  if (peso) tags.push(`<span>${escapar(peso)}</span>`);
  if (planilha.secao_rota) tags.push(`<span>${escapar(planilha.secao_rota)}</span>`);
  if (inativo) {
    tags.push(
      `<span class="documento-alerta">Link fora do ar na última verificação</span>`
    );
  }

  // O profiler grava "Nenhum" quando não tem o que dizer; repetir isso em
  // cada item seria ruído.
  const queixas = [planilha.erros_qualidade, planilha.avisos_qualidade]
    .map((texto) => String(texto || "").trim())
    .filter((texto) => texto && texto !== "Nenhum");

  const qualidade = queixas.length
    ? `<span class="planilha-qualidade">${escapar(queixas.join(" · "))}</span>`
    : "";

  const corpo = `
    <span class="documento-icone planilha-icone">${escapar(formato)}</span>
    <span class="documento-corpo">
      <span class="documento-titulo">${titulo}</span>
      <span class="documento-meta">${tags.join("")}</span>
      ${qualidade}
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

function renderizarLista(planilhas) {
  if (planilhas.length) {
    $("lista").innerHTML = planilhas.map(itemPlanilha).join("");
    return;
  }

  const buscando = $("filtro-busca").value.trim();
  const explicacao = buscando
    ? `<p class="empty-state">Nenhuma planilha encontrada para <strong>${escapar(buscando)}</strong>.
         <br>A busca cobre título, nome do arquivo, seção de origem, tipo e UF.</p>`
    : `<p class="empty-state">Nenhuma planilha encontrada para esses filtros.</p>`;

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
  $("resumo").innerHTML = "";
  $("resumo-resultados").textContent = "Sem conexão com a API.";
  $("contador").textContent = "—";
  $("paginacao").hidden = true;
}

// --------------------------------------------------------------------------
// Carregamento
// --------------------------------------------------------------------------

/** Opções de um filtro, já contadas e ordenadas pela API.
 *
 * Cada opção mostra quantos arquivos tem, e nenhuma que daria zero aparece —
 * a agregação é restrita ao tema, então formato e entidade do catálogo de
 * PDF simplesmente não constam.
 */
function preencherFiltro(id, contagem, rotularOpcao) {
  $(id).innerHTML = [...contagem]
    .map(
      ([valor, total]) =>
        `<option value="${escapar(valor)}">${escapar(rotularOpcao(valor))} (${numero(total)})</option>`
    )
    .join("");
}

/** Monta filtros e cartões a partir de cinco agregações do acervo. */
async function carregarFiltrosEResumo() {
  const doTema = { tema: TEMA_PLANILHAS };

  const [porFormato, porEntidade, porTipo, porLeitura, porSituacao] =
    await Promise.all([
      contagens("tipo_arquivo", doTema),
      contagens("entidade", doTema),
      contagens("tipo_documento", doTema),
      contagens("estruturado", doTema),
      contagens("ativo", doTema),
    ]);

  preencherFiltro("filtro-formato", porFormato, (f) => f.toUpperCase());
  preencherFiltro("filtro-entidade", porEntidade, (e) => e);
  preencherFiltro("filtro-tipo", porTipo, rotulo);

  // O total sai da soma por situação porque `ativo` é a única coluna que todo
  // registro de catálogo tem — o pipeline a escreve para os dois catálogos,
  // via `sim_ou_nao`. Somar por formato erraria para menos se algum link
  // tivesse chegado sem tipo de arquivo: a agregação descarta os nulos.
  const totais = {
    total: [...porSituacao.values()].reduce((a, b) => a + b, 0),
    csv: porFormato.get("csv") || 0,
    lidos: porLeitura.get("SIM") || 0,
    quebrados: porSituacao.get("NÃO") || 0,
  };

  $("resumo").innerHTML = CARTOES.map(
    (cartao) => `
      <button type="button" class="planilhas-cartao ${cartao.alerta ? "alerta" : ""}"
              data-cartao="${escapar(cartao.id)}">
        <span class="planilhas-cartao-numero">${numero(totais[cartao.id])}</span>
        <span class="planilhas-cartao-rotulo">${escapar(cartao.rotulo)}</span>
        <span class="planilhas-cartao-detalhe">${escapar(cartao.detalhe)}</span>
      </button>`
  ).join("");

  return totais.total;
}

async function carregarPlanilhas() {
  $("lista").innerHTML = `<p class="carregando">Carregando planilhas…</p>`;

  const params = parametrosFiltro();
  params.set("page", state.pagina);
  params.set("page_size", POR_PAGINA);

  const resposta = await pedir("/api/v1/documentos", params);

  state.total = resposta.total || 0;
  state.totalPaginas = resposta.total_pages || 0;

  renderizarLista(resposta.data || []);
  atualizarPaginacao();
  atualizarLinksExportacao();

  const plural = state.total === 1 ? "arquivo" : "arquivos";
  $("contador").textContent = `${numero(state.total)} ${plural}`;
  $("resumo-resultados").textContent = state.total
    ? `${numero(state.total)} ${plural} · clique em qualquer item para baixar da fonte.`
    : "Ajuste os filtros para encontrar planilhas.";
}

/** Filtro novo recomeça da primeira página: a antiga pode nem existir mais. */
async function aplicarFiltros() {
  state.pagina = 1;
  await comErro(carregarPlanilhas);
}

function limparFiltros() {
  $("filtro-busca").value = "";
  ["filtro-formato", "filtro-tipo", "filtro-entidade"].forEach((id) => {
    [...$(id).options].forEach((opcao) => (opcao.selected = false));
  });
  $("filtro-situacao").value = "";
  $("filtro-leitura").value = "";
}

/** Deixa nos filtros exatamente a consulta que produziu o número do cartão. */
function aplicarCartao(id) {
  const cartao = CARTOES.find((c) => c.id === id);
  if (!cartao) return;

  limparFiltros();

  if (cartao.filtros.ativo) $("filtro-situacao").value = cartao.filtros.ativo;
  if (cartao.filtros.estruturado) {
    $("filtro-leitura").value = cartao.filtros.estruturado;
  }
  if (cartao.filtros.tipo_arquivo) {
    [...$("filtro-formato").options].forEach((opcao) => {
      opcao.selected = opcao.value === cartao.filtros.tipo_arquivo;
    });
  }

  aplicarFiltros();
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
  await comErro(carregarPlanilhas);
  window.scrollTo({ top: 0, behavior: "smooth" });
}

async function main() {
  ["filtro-formato", "filtro-tipo", "filtro-entidade", "filtro-situacao", "filtro-leitura"]
    .forEach((id) => $(id).addEventListener("change", aplicarFiltros));
  $("filtro-busca").addEventListener("input", aoDigitar(aplicarFiltros));

  $("limpar").addEventListener("click", () => {
    limparFiltros();
    aplicarFiltros();
  });

  // Delegação: os cartões só existem depois que os totais chegam.
  $("resumo").addEventListener("click", (evento) => {
    const cartao = evento.target.closest("[data-cartao]");
    if (cartao) aplicarCartao(cartao.dataset.cartao);
  });

  $("pagina-anterior").addEventListener("click", () => irPara(state.pagina - 1));
  $("pagina-proxima").addEventListener("click", () => irPara(state.pagina + 1));

  // A lista não espera pelos filtros: são consultas independentes, e o acervo
  // aparecendo primeiro é o que a pessoa veio ver.
  await comErro(async () => {
    const [total] = await Promise.all([
      carregarFiltrosEResumo(),
      carregarPlanilhas(),
    ]);
    $("badge-api").textContent = total ? "API conectada" : "API sem planilhas";
  });
}

main();
