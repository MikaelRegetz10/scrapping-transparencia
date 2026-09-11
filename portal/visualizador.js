// Visualizador de dados: o conteúdo de uma planilha coletada, em grade.
//
// As páginas de catálogo listam links — o que existe, se respondeu, o que o
// profiler achou. Esta abre o que está dentro: as linhas que o pipeline
// gravou no Parquet, com as colunas que a entidade publicou, na ordem em que
// publicou. É a diferença entre saber que o arquivo de contratos existe e ler
// os contratos.
//
// O acervo é grande demais para caber no navegador — 2.600 conjuntos, quase
// 390 mil linhas —, então nada disto é carregado de uma vez: a API responde
// por um conjunto e por uma página de cada vez, e é ela quem busca, ordena e
// pagina. O que roda aqui é a grade.

import {
  $,
  API_BASE,
  aoDigitar,
  escapar,
  numero,
  pedir,
} from "./catalogo.js";

const POR_PAGINA_CONJUNTOS = 20;

// Largura das colunas, em pixels. O mínimo cabe um cabeçalho curto; o máximo
// impede que uma coluna de texto corrido — a descrição do objeto de um
// contrato, tipicamente — empurre todas as outras para fora da tela.
const LARGURA_MINIMA = 96;
const LARGURA_MAXIMA = 380;

// Quantas linhas da página são medidas para estimar a largura de uma coluna.
// A amostra basta: medir as 500 daria a mesma largura por muito mais trabalho.
const LINHAS_AMOSTRADAS = 40;

// Tipos que o DuckDB devolve para coluna numérica. Só elas são alinhadas à
// direita: quase todo dado destes portais chega como texto, e alinhar
// "1422892,8" à direita fingiria uma tipagem que o arquivo não tem.
const TIPOS_NUMERICOS = /^(BIGINT|INTEGER|SMALLINT|TINYINT|HUGEINT|DOUBLE|FLOAT|DECIMAL|REAL)/i;

const state = {
  // O conjunto aberto, como a API o descreve. Nulo enquanto nenhum foi aberto.
  conjunto: null,
  conjuntos: { pagina: 1, totalPaginas: 0 },
  grade: { pagina: 1, totalPaginas: 0, ordenar: null, direcao: "asc" },
  // Célula selecionada, em índices da página corrente.
  selecao: null,
  // Largura de cada coluna. Sobrevive à troca de página e à ordenação — só
  // zera quando o conjunto muda —, senão o ajuste manual se perderia a cada
  // clique.
  larguras: [],
};

// --------------------------------------------------------------------------
// Nomes
// --------------------------------------------------------------------------

/** Nome legível de um valor sanitizado do Parquet ("execucao_orcamentaria"). */
function rotulo(valor) {
  return String(valor || "")
    .replaceAll("_", " ")
    .replace(/\s+/g, " ")
    .trim();
}

/** Título de um conjunto, sem o prefixo da entidade que já é mostrada ao lado. */
function tituloDoConjunto(conjunto) {
  let texto = rotulo(conjunto.nome);
  const prefixo = `${conjunto.entidade} `.toLowerCase();

  if (texto.toLowerCase().startsWith(prefixo)) {
    texto = texto.slice(prefixo.length);
  }

  return texto.charAt(0).toUpperCase() + texto.slice(1);
}

/** "A", "B" … "AA": o endereço da coluna, como em qualquer planilha. */
function letraDaColuna(indice) {
  let letra = "";
  let resto = indice;

  do {
    letra = String.fromCharCode(65 + (resto % 26)) + letra;
    resto = Math.floor(resto / 26) - 1;
  } while (resto >= 0);

  return letra;
}

// --------------------------------------------------------------------------
// Lista de conjuntos
// --------------------------------------------------------------------------

function parametrosConjuntos() {
  const params = new URLSearchParams();

  const busca = $("filtro-busca").value.trim();
  if (busca) params.set("search", busca);

  const entidade = $("filtro-entidade").value;
  if (entidade) params.set("entidade", entidade);

  const ano = $("filtro-ano").value;
  if (ano) params.set("ano", ano);

  params.set("ordenar", $("filtro-ordem").value);
  return params;
}

function itemConjunto(conjunto) {
  // Dois conjuntos podem ter o mesmo nome em exercícios diferentes — é o caso
  // de quase toda coleta da ABDI. Sem ano e UF na linha eles ficariam
  // indistinguíveis na lista.
  const tags = [
    `<span class="conjunto-tag">${escapar(conjunto.entidade)}</span>`,
    `<span>${escapar(rotulo(conjunto.tipo_documento))}</span>`,
    `<span>${escapar(conjunto.ano)}</span>`,
    `<span>${escapar(conjunto.uf)}</span>`,
  ];

  const aberto = state.conjunto?.arquivo === conjunto.arquivo;

  return `
    <button type="button" class="conjunto-item ${aberto ? "aberto" : ""}"
            data-arquivo="${escapar(conjunto.arquivo)}">
      <span class="conjunto-corpo">
        <span class="conjunto-nome">${escapar(tituloDoConjunto(conjunto))}</span>
        <span class="conjunto-meta">${tags.join("")}</span>
      </span>
      <span class="conjunto-tamanho">
        <strong>${numero(conjunto.linhas)}</strong>
        ${numero(conjunto.colunas)} ${conjunto.colunas === 1 ? "coluna" : "colunas"}
      </span>
    </button>`;
}

/** O que dizer quando a busca não achou conjunto nenhum.
 *
 * Quem chega da página de planilhas chega com o título de um arquivo do
 * catálogo, e nem todo título casa com o nome de um conjunto: o pipeline
 * nomeia a partição pelo título sanitizado, e nem todo arquivo do catálogo
 * teve o conteúdo lido pelo profiler. Um "nenhum resultado" seco deixaria a
 * pessoa achando que o acervo está vazio.
 */
function estadoVazio() {
  const busca = $("filtro-busca").value.trim();

  if (!busca) {
    return `<p class="empty-state">Nenhum conjunto encontrado para esses filtros.</p>`;
  }

  return `
    <p class="empty-state">
      Nenhum conjunto do acervo casa com <strong>${escapar(busca)}</strong>.
      <br>Pode ser que o conteúdo desse arquivo não tenha sido lido na coleta,
      ou que o nome dele no acervo seja outro.
      <br><button type="button" class="btn btn-secondary" id="limpar-busca">
        Ver todos os conjuntos
      </button>
    </p>`;
}

async function carregarConjuntos() {
  $("conjuntos-lista").innerHTML = `<p class="carregando">Carregando conjuntos…</p>`;

  const params = parametrosConjuntos();
  params.set("page", state.conjuntos.pagina);
  params.set("page_size", POR_PAGINA_CONJUNTOS);

  const resposta = await pedir("/api/v1/conjuntos", params);
  const conjuntos = resposta.data || [];

  state.conjuntos.totalPaginas = resposta.total_pages || 0;

  $("conjuntos-lista").innerHTML = conjuntos.length
    ? conjuntos.map(itemConjunto).join("")
    : estadoVazio();

  const plural = resposta.total === 1 ? "conjunto" : "conjuntos";
  $("conjuntos-contador").textContent = `${numero(resposta.total)} ${plural}`;
  $("conjuntos-resumo").textContent = resposta.total
    ? "Cada conjunto é o conteúdo de uma planilha coletada. Clique para abrir."
    : "Ajuste a busca para encontrar um conjunto.";

  const temPaginas = state.conjuntos.totalPaginas > 1;
  $("conjuntos-paginacao").hidden = !temPaginas;
  if (temPaginas) {
    $("conjuntos-paginacao-texto").textContent =
      `Página ${numero(state.conjuntos.pagina)} de ${numero(state.conjuntos.totalPaginas)}`;
    $("conjuntos-anterior").disabled = state.conjuntos.pagina <= 1;
    $("conjuntos-proxima").disabled =
      state.conjuntos.pagina >= state.conjuntos.totalPaginas;
  }

  return conjuntos;
}

// --------------------------------------------------------------------------
// A grade
// --------------------------------------------------------------------------

/** Largura inicial de uma coluna, medida no cabeçalho e numa amostra de linhas.
 *
 * Aproxima o texto por contagem de caracteres. É grosseiro, e é o suficiente:
 * o objetivo não é caber tudo — coluna de texto corrido nunca cabe —, é que
 * a maioria das colunas abra num tamanho que não precise de ajuste.
 */
function larguraSugerida(coluna, linhas, indice) {
  let caracteres = coluna.rotulo.length + 3;

  for (const linha of linhas.slice(0, LINHAS_AMOSTRADAS)) {
    const celula = linha[indice];
    if (celula !== null && celula !== undefined) {
      caracteres = Math.max(caracteres, String(celula).length);
    }
  }

  return Math.min(LARGURA_MAXIMA, Math.max(LARGURA_MINIMA, caracteres * 7.5 + 24));
}

function cabecalhoDaGrade(colunas) {
  const letras = colunas
    .map((_, indice) => `<th data-coluna="${indice}">${letraDaColuna(indice)}</th>`)
    .join("");

  const nomes = colunas
    .map((coluna, indice) => {
      const ordenada = state.grade.ordenar === coluna.nome;
      const seta = ordenada
        ? `<span class="grade-seta">${state.grade.direcao === "asc" ? "▲" : "▼"}</span>`
        : "";

      // O rótulo aparece; quem vai no `ordenar` é o nome real da coluna no
      // arquivo, que às vezes traz BOM e aspas herdados do CSV de origem.
      return `
        <th data-coluna="${indice}">
          <button type="button" class="grade-ordenar" data-ordenar="${escapar(coluna.nome)}"
                  title="Ordenar por ${escapar(coluna.rotulo)}">
            <span class="grade-ordenar-nome">${escapar(coluna.rotulo)}</span>${seta}
          </button>
          <span class="grade-alca" data-alca="${indice}"></span>
        </th>`;
    })
    .join("");

  return `
    <colgroup>
      <col style="width: 56px">
      ${state.larguras.map((largura) => `<col style="width: ${largura}px">`).join("")}
    </colgroup>
    <thead>
      <tr class="grade-letras"><th class="grade-canto"></th>${letras}</tr>
      <tr class="grade-nomes"><th class="grade-canto"></th>${nomes}</tr>
    </thead>`;
}

function corpoDaGrade(resposta) {
  if (!resposta.linhas.length) {
    return "";
  }

  // O número é o da linha no conjunto, não o da linha na tela: numa página 3
  // a primeira linha é a 201, e chamá-la de 1 seria mentir sobre onde ela
  // está no arquivo.
  const primeira = (resposta.page - 1) * resposta.page_size + 1;
  const numericas = resposta.colunas.map((coluna) => TIPOS_NUMERICOS.test(coluna.tipo));

  const linhas = resposta.linhas.map((linha, indiceLinha) => {
    const celulas = linha
      .map((valor, indiceColuna) => {
        const vazia = valor === null || valor === undefined || valor === "";
        const classes = [
          numericas[indiceColuna] ? "numerica" : "",
          vazia ? "nulo" : "",
        ]
          .filter(Boolean)
          .join(" ");

        const texto = vazia ? "" : String(valor);
        // Só o valor que a coluna não mostra inteiro ganha tooltip: repetir
        // cada célula num atributo dobraria o tamanho da página à toa.
        const dica = texto.length > 30 ? ` title="${escapar(texto)}"` : "";

        return `<td class="${classes}" data-linha="${indiceLinha}" data-coluna="${indiceColuna}"${dica}>${escapar(texto)}</td>`;
      })
      .join("");

    return `<tr><th class="grade-numero" data-linha="${indiceLinha}">${numero(primeira + indiceLinha)}</th>${celulas}</tr>`;
  });

  return `<tbody>${linhas.join("")}</tbody>`;
}

function desenharGrade(resposta) {
  if (!resposta.linhas.length) {
    const filtrando = $("grade-filtro").value.trim();
    $("grade-envelope").innerHTML = filtrando
      ? `<p class="grade-vazia">Nenhuma linha contém <strong>${escapar(filtrando)}</strong>.</p>`
      : `<p class="grade-vazia">Este conjunto está vazio.</p>`;
    return;
  }

  // O envelope troca a tabela por um parágrafo quando está carregando ou
  // vazio; devolver a tabela é mais simples que manter as duas vivas.
  if (!$("grade")) {
    $("grade-envelope").innerHTML = `<table class="grade" id="grade"></table>`;
  }

  $("grade").innerHTML = cabecalhoDaGrade(resposta.colunas) + corpoDaGrade(resposta);
}

function limparSelecao() {
  state.selecao = null;
  $("celula-endereco").textContent = "—";
  $("celula-valor").textContent = "Clique numa célula para ver o valor inteiro.";
  $("celula-valor").classList.add("vazio");
  $("celula-copiar").disabled = true;
}

function selecionar(indiceLinha, indiceColuna) {
  const grade = $("grade");
  if (!grade) return;

  const celula = grade.querySelector(
    `td[data-linha="${indiceLinha}"][data-coluna="${indiceColuna}"]`
  );
  if (!celula) return;

  grade.querySelectorAll(".selecionada, .ativa").forEach((elemento) => {
    elemento.classList.remove("selecionada", "ativa");
  });

  celula.classList.add("selecionada");
  celula.scrollIntoView({ block: "nearest", inline: "nearest" });

  // Cabeçalhos da coluna e da linha acesos: numa tabela larga, a célula
  // sozinha não diz de qual coluna ela é.
  grade
    .querySelectorAll(`th[data-coluna="${indiceColuna}"], th[data-linha="${indiceLinha}"]`)
    .forEach((th) => th.classList.add("ativa"));

  const valor = state.conjunto.linhas[indiceLinha][indiceColuna];
  const vazio = valor === null || valor === undefined || valor === "";
  const primeira =
    (state.conjunto.page - 1) * state.conjunto.page_size + 1;

  state.selecao = { linha: indiceLinha, coluna: indiceColuna };
  $("celula-endereco").textContent =
    `${letraDaColuna(indiceColuna)}${primeira + indiceLinha}`;
  $("celula-valor").textContent = vazio
    ? `(vazio) · ${state.conjunto.colunas[indiceColuna].rotulo}`
    : String(valor);
  $("celula-valor").classList.toggle("vazio", vazio);
  $("celula-copiar").disabled = vazio;
}

/** Move a seleção, respeitando as bordas da página exibida. */
function mover(deltaLinha, deltaColuna) {
  if (!state.conjunto?.linhas.length) return;

  const atual = state.selecao || { linha: 0, coluna: 0 };
  const linha = Math.min(
    Math.max(0, atual.linha + deltaLinha),
    state.conjunto.linhas.length - 1
  );
  const coluna = Math.min(
    Math.max(0, atual.coluna + deltaColuna),
    state.conjunto.colunas.length - 1
  );

  selecionar(linha, coluna);
}

async function copiarSelecao() {
  if (!state.selecao) return;

  const { linha, coluna } = state.selecao;
  const texto = String(state.conjunto.linhas[linha][coluna] ?? "");

  try {
    await navigator.clipboard.writeText(texto);
  } catch {
    // A área de transferência exige contexto seguro, e o portal também roda
    // servido por http simples numa rede interna. O caminho antigo funciona
    // nos dois.
    const campo = document.createElement("textarea");
    campo.value = texto;
    campo.style.position = "fixed";
    campo.style.opacity = "0";
    document.body.appendChild(campo);
    campo.select();
    document.execCommand("copy");
    campo.remove();
  }

  const botao = $("celula-copiar");
  botao.textContent = "Copiado";
  setTimeout(() => (botao.textContent = "Copiar"), 1200);
}

// --------------------------------------------------------------------------
// Redimensionamento das colunas
// --------------------------------------------------------------------------

/** Arrasta a divisa entre duas colunas, como numa planilha.
 *
 * Mexe no <col> e não no <th>: com `table-layout: fixed` é o colgroup que
 * manda na largura, e assim uma coluna só é repintada por arrasto.
 */
function iniciarRedimensionamento(evento, indice) {
  evento.preventDefault();

  const colunas = $("grade").querySelectorAll("colgroup col");
  const col = colunas[indice + 1];
  if (!col) return;

  const xInicial = evento.clientX;
  const larguraInicial = state.larguras[indice];
  document.body.classList.add("grade-redimensionando");

  const arrastar = (movimento) => {
    const largura = Math.max(
      LARGURA_MINIMA,
      larguraInicial + (movimento.clientX - xInicial)
    );
    state.larguras[indice] = largura;
    col.style.width = `${largura}px`;
  };

  const soltar = () => {
    document.body.classList.remove("grade-redimensionando");
    document.removeEventListener("mousemove", arrastar);
    document.removeEventListener("mouseup", soltar);
  };

  document.addEventListener("mousemove", arrastar);
  document.addEventListener("mouseup", soltar);
}

// --------------------------------------------------------------------------
// Carregamento do conjunto
// --------------------------------------------------------------------------

function porPagina() {
  return Number($("grade-por-pagina").value) || 100;
}

function parametrosGrade() {
  const params = new URLSearchParams({ arquivo: state.conjunto.arquivo });

  const busca = $("grade-filtro").value.trim();
  if (busca) params.set("search", busca);

  if (state.grade.ordenar) {
    params.set("ordenar", state.grade.ordenar);
    params.set("direcao", state.grade.direcao);
  }

  return params;
}

function atualizarLinksExportacao() {
  for (const formato of ["csv", "xlsx", "json"]) {
    const params = parametrosGrade();
    params.set("formato", formato);
    $(`exportar-${formato}`).href =
      `${API_BASE}/api/v1/conjuntos/exportacao?${params}`;
  }
}

function atualizarCabecalhoDoConjunto(conjunto) {
  $("grade-titulo").textContent = tituloDoConjunto(conjunto);

  const filtrado = conjunto.total_filtrado;
  const contagem =
    filtrado === conjunto.total
      ? `${numero(conjunto.total)} linhas`
      : `${numero(filtrado)} de ${numero(conjunto.total)} linhas`;

  const total = conjunto.colunas.length;
  const colunas = `${numero(total)} ${total === 1 ? "coluna" : "colunas"}`;

  $("grade-meta").textContent = [
    conjunto.entidade,
    rotulo(conjunto.tipo_documento),
    `exercício ${conjunto.ano}`,
    conjunto.uf,
    `${contagem} × ${colunas}`,
  ].join(" · ");

  $("seletor-atual").textContent =
    `${tituloDoConjunto(conjunto)} · ${conjunto.entidade} · ${conjunto.ano}`;
}

function atualizarPaginacaoDaGrade(conjunto) {
  const temPaginas = state.grade.totalPaginas > 1;
  $("grade-paginacao").hidden = !temPaginas;
  if (!temPaginas) return;

  const primeira = (state.grade.pagina - 1) * conjunto.page_size + 1;
  const ultima = primeira + conjunto.linhas.length - 1;

  $("grade-paginacao-texto").textContent =
    `Linhas ${numero(primeira)}–${numero(ultima)} de ${numero(conjunto.total_filtrado)}`;
  $("grade-anterior").disabled = state.grade.pagina <= 1;
  $("grade-proxima").disabled = state.grade.pagina >= state.grade.totalPaginas;
}

async function carregarGrade() {
  const params = parametrosGrade();
  params.set("page", state.grade.pagina);
  params.set("page_size", porPagina());

  let conjunto;
  try {
    conjunto = await pedir("/api/v1/conjuntos/dados", params);
  } catch (erro) {
    $("grade-envelope").innerHTML =
      `<p class="grade-vazia">Não foi possível abrir este conjunto. ${escapar(erro.message)}</p>`;
    $("grade-paginacao").hidden = true;
    return;
  }

  // As larguras são do conjunto, não da página: mantê-las ao paginar é o que
  // impede a tabela de dançar a cada "próxima".
  if (state.conjunto?.arquivo !== conjunto.arquivo || !state.larguras.length) {
    state.larguras = conjunto.colunas.map((coluna, indice) =>
      larguraSugerida(coluna, conjunto.linhas, indice)
    );
  }

  state.conjunto = conjunto;
  state.grade.totalPaginas = conjunto.total_pages || 0;

  desenharGrade(conjunto);
  limparSelecao();
  atualizarCabecalhoDoConjunto(conjunto);
  atualizarPaginacaoDaGrade(conjunto);
  atualizarLinksExportacao();
}

function barraDePreenchimento(coluna) {
  const porcento = Math.round((coluna.preenchimento || 0) * 100);
  const parcial = porcento < 100 ? "parcial" : "";

  return `
    <span class="dicionario-barra ${parcial}" role="img"
          aria-label="${porcento}% preenchida">
      <span style="width: ${porcento}%"></span>
    </span>`;
}

async function carregarDicionario() {
  const conteudo = $("dicionario-conteudo");
  conteudo.innerHTML = `<p class="carregando">Medindo as colunas…</p>`;

  let dicionario;
  try {
    dicionario = await pedir(
      "/api/v1/conjuntos/colunas",
      new URLSearchParams({ arquivo: state.conjunto.arquivo })
    );
  } catch {
    conteudo.innerHTML =
      `<p class="empty-state">Não foi possível medir as colunas deste conjunto.</p>`;
    return;
  }

  const linhas = dicionario.colunas
    .map(
      (coluna, indice) => `
        <tr>
          <td>${letraDaColuna(indice)}</td>
          <td class="dicionario-coluna">${escapar(coluna.rotulo)}</td>
          <td class="dicionario-tipo">${escapar(coluna.tipo)}</td>
          <td>${barraDePreenchimento(coluna)}</td>
          <td class="numero">${numero(coluna.preenchidas)} de ${numero(dicionario.total)}</td>
          <td class="numero">${numero(coluna.distintos)}</td>
        </tr>`
    )
    .join("");

  conteudo.innerHTML = `
    <div class="table-responsive">
      <table class="dicionario-tabela">
        <thead>
          <tr>
            <th>Col.</th>
            <th>Coluna</th>
            <th>Tipo</th>
            <th>Preenchimento</th>
            <th>Linhas com valor</th>
            <th>Valores distintos</th>
          </tr>
        </thead>
        <tbody>${linhas}</tbody>
      </table>
    </div>`;
}

/** Abre um conjunto: recomeça a grade do zero e guarda a escolha na URL. */
async function abrirConjunto(arquivo) {
  if (!arquivo) return;

  state.conjunto = { arquivo, linhas: [], colunas: [] };
  state.grade = { pagina: 1, totalPaginas: 0, ordenar: null, direcao: "asc" };
  state.larguras = [];
  $("grade-filtro").value = "";

  $("grade-card").hidden = false;
  $("dicionario-card").hidden = false;
  $("dicionario-card").open = false;
  $("seletor").open = false;
  $("grade-envelope").innerHTML = `<p class="carregando">Abrindo o conjunto…</p>`;

  // A URL passa a apontar para este conjunto: assim ele pode ser mandado a
  // alguém, e o F5 não devolve a tela em branco.
  const url = new URL(location.href);
  url.searchParams.set("arquivo", arquivo);
  history.replaceState(null, "", url);

  await Promise.all([carregarGrade(), carregarDicionario()]);

  document
    .querySelectorAll(".conjunto-item")
    .forEach((item) =>
      item.classList.toggle("aberto", item.dataset.arquivo === arquivo)
    );
}

// --------------------------------------------------------------------------
// Eventos
// --------------------------------------------------------------------------

async function comErro(acao) {
  try {
    await acao();
    $("aviso-erro").hidden = true;
  } catch (erro) {
    $("aviso-erro").hidden = false;
    $("aviso-erro-texto").textContent =
      `${erro.message} Confira se ela está no ar em ${API_BASE} e rode, na raiz do projeto:`;
    $("conjuntos-lista").innerHTML = "";
    $("conjuntos-resumo").textContent = "Sem conexão com a API.";
    $("conjuntos-contador").textContent = "—";
    $("conjuntos-paginacao").hidden = true;
  }
}

/** Clique no cabeçalho: ascendente, descendente, sem ordenação, e recomeça. */
function ordenarPor(coluna) {
  const { ordenar, direcao } = state.grade;

  if (ordenar !== coluna) {
    state.grade.ordenar = coluna;
    state.grade.direcao = "asc";
  } else if (direcao === "asc") {
    state.grade.direcao = "desc";
  } else {
    state.grade.ordenar = null;
    state.grade.direcao = "asc";
  }

  state.grade.pagina = 1;
  carregarGrade();
}

function ligarEventosDaGrade() {
  const envelope = $("grade-envelope");

  envelope.addEventListener("mousedown", (evento) => {
    const alca = evento.target.closest("[data-alca]");
    if (alca) iniciarRedimensionamento(evento, Number(alca.dataset.alca));
  });

  envelope.addEventListener("click", (evento) => {
    const ordenar = evento.target.closest("[data-ordenar]");
    if (ordenar) {
      ordenarPor(ordenar.dataset.ordenar);
      return;
    }

    const celula = evento.target.closest("td[data-linha]");
    if (celula) {
      envelope.focus({ preventScroll: true });
      selecionar(Number(celula.dataset.linha), Number(celula.dataset.coluna));
    }
  });

  // Navegar de célula em célula pelo teclado é o que separa uma grade de uma
  // tabela: sem isso, ler a coluna 20 exige o mouse a cada linha.
  envelope.addEventListener("keydown", (evento) => {
    const passos = {
      ArrowUp: [-1, 0],
      ArrowDown: [1, 0],
      ArrowLeft: [0, -1],
      ArrowRight: [0, 1],
    };

    if (passos[evento.key]) {
      evento.preventDefault();
      mover(...passos[evento.key]);
      return;
    }

    if (evento.key === "Home") {
      evento.preventDefault();
      selecionar(state.selecao?.linha || 0, 0);
    } else if (evento.key === "End") {
      evento.preventDefault();
      selecionar(state.selecao?.linha || 0, state.conjunto.colunas.length - 1);
    } else if (evento.key === "c" && (evento.ctrlKey || evento.metaKey)) {
      // Só assume o Ctrl+C quando há célula selecionada; do contrário atrapalha
      // quem selecionou texto com o mouse.
      if (state.selecao) {
        evento.preventDefault();
        copiarSelecao();
      }
    }
  });

  $("celula-copiar").addEventListener("click", copiarSelecao);
}

function ligarEventosDoSeletor() {
  const recarregar = () => {
    state.conjuntos.pagina = 1;
    comErro(carregarConjuntos);
  };

  $("filtro-busca").addEventListener("input", aoDigitar(recarregar));
  ["filtro-entidade", "filtro-ano", "filtro-ordem"].forEach((id) =>
    $(id).addEventListener("change", recarregar)
  );

  // Delegação: os itens e o estado vazio só existem depois que a listagem
  // chega.
  $("conjuntos-lista").addEventListener("click", (evento) => {
    if (evento.target.closest("#limpar-busca")) {
      $("filtro-busca").value = "";
      recarregar();
      return;
    }

    const item = evento.target.closest("[data-arquivo]");
    if (item) abrirConjunto(item.dataset.arquivo);
  });

  const irPara = (pagina) => {
    state.conjuntos.pagina = Math.min(
      Math.max(1, pagina),
      state.conjuntos.totalPaginas || 1
    );
    comErro(carregarConjuntos);
  };

  $("conjuntos-anterior").addEventListener("click", () =>
    irPara(state.conjuntos.pagina - 1)
  );
  $("conjuntos-proxima").addEventListener("click", () =>
    irPara(state.conjuntos.pagina + 1)
  );
}

function ligarEventosDaPaginacao() {
  $("grade-filtro").addEventListener(
    "input",
    aoDigitar(() => {
      state.grade.pagina = 1;
      carregarGrade();
    })
  );

  $("grade-por-pagina").addEventListener("change", () => {
    state.grade.pagina = 1;
    carregarGrade();
  });

  const irPara = (pagina) => {
    state.grade.pagina = Math.min(
      Math.max(1, pagina),
      state.grade.totalPaginas || 1
    );
    carregarGrade().then(() =>
      $("grade-envelope").scrollTo({ top: 0, behavior: "smooth" })
    );
  };

  $("grade-anterior").addEventListener("click", () => irPara(state.grade.pagina - 1));
  $("grade-proxima").addEventListener("click", () => irPara(state.grade.pagina + 1));
}

/** Entidades e exercícios do acervo, para os filtros do seletor. */
async function carregarFiltros() {
  const opcoes = await pedir("/api/v1/filtros", new URLSearchParams());

  $("filtro-entidade").innerHTML =
    `<option value="">Todas</option>` +
    (opcoes.entidades || [])
      .map((e) => `<option value="${escapar(e)}">${escapar(e)}</option>`)
      .join("");

  $("filtro-ano").innerHTML =
    `<option value="">Todos</option>` +
    (opcoes.anos || [])
      .map((a) => `<option value="${escapar(a)}">${escapar(a)}</option>`)
      .join("");
}

async function main() {
  ligarEventosDoSeletor();
  ligarEventosDaGrade();
  ligarEventosDaPaginacao();
  limparSelecao();

  // Duas portas de entrada, ambas vindas da página de planilhas: `arquivo`
  // abre um conjunto direto, `busca` chega com o título de um item do
  // catálogo e deixa o seletor pré-filtrado por ele.
  const parametros = new URLSearchParams(location.search);
  const arquivo = parametros.get("arquivo");
  const busca = parametros.get("busca");

  if (busca) $("filtro-busca").value = busca;
  await comErro(carregarFiltros);

  // O select fica em "Todas" se a entidade pedida não estiver entre as
  // opções — e a busca por texto ainda restringe a lista.
  if (parametros.get("entidade")) {
    $("filtro-entidade").value = parametros.get("entidade");
  }

  await comErro(async () => {
    const conjuntos = await carregarConjuntos();
    $("badge-api").textContent = conjuntos.length
      ? "API conectada"
      : "API sem conjuntos";

    if (arquivo) {
      await abrirConjunto(arquivo);
    } else if (busca && conjuntos.length === 1) {
      // Veio da página de planilhas com um título só, e ele casou com um
      // conjunto só: não há escolha a fazer.
      await abrirConjunto(conjuntos[0].arquivo);
    }
  });
}

main();
