// Dicionário de dados: o que se sabe das colunas de um conjunto do acervo.
//
// O visualizador já trazia esta medida num card lateral, como nota de rodapé
// da grade. Aqui ela é o assunto, e por isso vai além da tabela: a página diz
// também o que a medida *significa* — que uma coluna com um valor só não
// distingue linha nenhuma, que uma coluna vazia é campo que a entidade
// publicou em branco, que um conjunto inteiro em VARCHAR é uma fonte que não
// tipou nada.
//
// Nada disto é descrição oficial de campo, e a página repete isso onde for
// preciso. As entidades do Sistema S publicam a planilha e não publicam o
// dicionário; o que existe é o que se mede, e medida não é significado.

import {
  $,
  API_BASE,
  aoDigitar,
  escapar,
  numero,
  pedir,
} from "./catalogo.js";

const POR_PAGINA_CONJUNTOS = 20;

// Abaixo disto uma coluna conta como preenchida só em parte. Metade é um
// corte arbitrário, e é o que separa "faltam alguns" de "quase não tem".
const LIMITE_PARCIAL = 0.5;

// Até quantos valores distintos uma coluna é lida como categoria — sexo,
// modalidade, situação — em vez de campo livre. Acima disso a variedade já
// não cabe num filtro, e dizer "poucos valores" seria mentira.
const MAXIMO_CATEGORIAS = 25;

// A cor de cada família de tipo, na faixa e nas etiquetas. São as mesmas
// cinco que o `tipo_legivel` do core produz; qualquer outra cai no cinza.
const CORES_DE_TIPO = {
  VARCHAR: "#0284c7",
  INTEGER: "#7c3aed",
  DOUBLE: "#db2777",
  "DATE/TIME": "#059669",
  BOOLEAN: "#d97706",
};

const state = {
  conjunto: null,
  dicionario: null,
  conjuntos: { pagina: 1, totalPaginas: 0 },
  // A página corrente da listagem, guardada para o cabeçalho do painel poder
  // dizer entidade, exercício e UF sem uma segunda ida à API: o clique já
  // aconteceu sobre um item que trazia tudo isso.
  listagem: [],
};

/** O conjunto da listagem corrente, pelo caminho do arquivo. */
function conjuntoDaLista(arquivo) {
  return state.listagem.find((c) => c.arquivo === arquivo) || null;
}

/** Os metadados de um conjunto lidos do próprio caminho da partição.
 *
 * O caminho Hive — `tema=X/entidade=Y/.../nome.parquet` — carrega tudo o que
 * o cabeçalho precisa dizer. Sem isto, um link direto para um conjunto que
 * não está na página corrente da listagem não teria de onde tirar entidade e
 * exercício, e imprimiria o caminho cru no lugar do título.
 */
function conjuntoDoCaminho(arquivo) {
  const partes = String(arquivo || "").split("/");
  const nome = partes.pop() || "";
  const campos = {};

  for (const parte of partes) {
    const igual = parte.indexOf("=");
    if (igual > 0) campos[parte.slice(0, igual)] = parte.slice(igual + 1);
  }

  if (!campos.entidade) return null;

  return {
    arquivo,
    nome: nome.replace(/\.parquet$/, ""),
    tema: campos.tema || "",
    entidade: campos.entidade,
    tipo_documento: campos.tipo_documento || "",
    ano: campos.ano || "",
    uf: campos.uf || "",
  };
}

/** O conjunto, da listagem se estiver lá, do caminho se não estiver. */
function descreverConjunto(arquivo) {
  return conjuntoDaLista(arquivo) || conjuntoDoCaminho(arquivo);
}

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

function porcento(fracao) {
  return `${Math.round((fracao || 0) * 100)}%`;
}

// --------------------------------------------------------------------------
// O diagnóstico de uma coluna
// --------------------------------------------------------------------------

/** O que a medida de uma coluna revela sobre ela.
 *
 * Cada caso aqui é uma leitura que a tabela crua não dá. "193 de 193, 1 valor
 * distinto" está correto e não diz nada; "um valor só em todas as linhas —
 * não distingue uma linha da outra" é a mesma medida virada para quem lê.
 *
 * A ordem importa: uma coluna vazia também tem zero distintos, e seria
 * descrita como constante se o vazio não viesse antes.
 */
function diagnosticar(coluna, total) {
  const { preenchidas = 0, distintos = 0 } = coluna;

  if (total === 0) {
    return null;
  }

  if (preenchidas === 0) {
    return {
      chave: "vazia",
      grau: "alerta",
      rotulo: "Sem nenhum valor",
      explicacao:
        "A coluna existe no arquivo e não tem uma linha preenchida. " +
        "O campo foi publicado em branco na fonte.",
    };
  }

  if (distintos === 1) {
    return {
      chave: "constante",
      grau: "aviso",
      rotulo: "Um valor só",
      explicacao:
        "Todas as linhas preenchidas repetem o mesmo valor. A coluna não " +
        "distingue uma linha da outra — serve de rótulo do conjunto, não de dado.",
    };
  }

  if (preenchidas / total < LIMITE_PARCIAL) {
    return {
      chave: "parcial",
      grau: "alerta",
      rotulo: "Preenchida em parte",
      explicacao:
        `Só ${porcento(preenchidas / total)} das linhas têm valor. Contas ` +
        "feitas sobre esta coluna cobrem uma fração do conjunto, não ele todo.",
    };
  }

  if (distintos === preenchidas && preenchidas === total && total > 1) {
    return {
      chave: "identificador",
      grau: "nota",
      rotulo: "Um valor por linha",
      explicacao:
        "Nenhum valor se repete. É o comportamento de um identificador — " +
        "número de processo, de contrato, de instrumento.",
    };
  }

  if (distintos <= MAXIMO_CATEGORIAS && distintos < preenchidas / 2) {
    return {
      chave: "categoria",
      grau: "nota",
      rotulo: `${numero(distintos)} categorias`,
      explicacao:
        "Poucos valores, muito repetidos. A coluna funciona como " +
        "classificação, e dá um filtro útil sobre o conjunto.",
    };
  }

  return null;
}

/** Só os diagnósticos que pedem atenção — os que o contador do resumo conta. */
function eRessalva(diagnostico) {
  return Boolean(diagnostico) && diagnostico.grau !== "nota";
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

  const uf = $("filtro-uf").value;
  if (uf) params.set("uf", uf);

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
        <strong>${numero(conjunto.colunas)}</strong>
        ${conjunto.colunas === 1 ? "coluna" : "colunas"}
      </span>
    </button>`;
}

function estadoVazio() {
  const busca = $("filtro-busca").value.trim();

  if (!busca) {
    return `<p class="empty-state">Nenhum conjunto encontrado para esses filtros.</p>`;
  }

  return `
    <p class="empty-state">
      Nenhum conjunto do acervo casa com <strong>${escapar(busca)}</strong>.
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
  state.listagem = conjuntos;

  $("conjuntos-lista").innerHTML = conjuntos.length
    ? conjuntos.map(itemConjunto).join("")
    : estadoVazio();

  const plural = resposta.total === 1 ? "conjunto" : "conjuntos";
  $("conjuntos-contador").textContent = `${numero(resposta.total)} ${plural}`;
  $("conjuntos-resumo").textContent = resposta.total
    ? "Clique num conjunto para ver o que se sabe das colunas dele."
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
// O resumo do conjunto
// --------------------------------------------------------------------------

/** Quantas colunas há de cada família de tipo, da mais comum para a menos. */
function contarTipos(colunas) {
  const contagem = new Map();

  for (const coluna of colunas) {
    contagem.set(coluna.tipo, (contagem.get(coluna.tipo) || 0) + 1);
  }

  return [...contagem.entries()].sort((a, b) => b[1] - a[1]);
}

/** A faixa de tipos e sua legenda.
 *
 * Num acervo raspado de portal, a proporção de VARCHAR é o indicador mais
 * direto de quanto a fonte tipou os próprios dados — e quase sempre a
 * resposta é "nada". A faixa mostra isso antes de a pessoa ler linha alguma
 * da tabela.
 */
function desenharTipos(colunas) {
  const tipos = contarTipos(colunas);
  const total = colunas.length;

  $("tipos-faixa").innerHTML = tipos
    .map(([tipo, quantas]) => {
      const cor = CORES_DE_TIPO[tipo] || "#94a3b8";
      const largura = (quantas / total) * 100;
      return `<span class="tipos-fatia" style="width:${largura}%;background:${cor}"
                    title="${escapar(tipo)}: ${quantas} de ${total}"></span>`;
    })
    .join("");

  $("tipos-legenda").innerHTML = tipos
    .map(([tipo, quantas]) => {
      const cor = CORES_DE_TIPO[tipo] || "#94a3b8";
      return `
        <span class="tipos-item">
          <span class="tipos-ponto" style="background:${cor}"></span>
          <strong>${escapar(tipo)}</strong>
          <span class="tipos-conta">${numero(quantas)}</span>
        </span>`;
    })
    .join("");
}

function desenharResumo(dicionario, conjunto) {
  const colunas = dicionario.colunas;
  const total = dicionario.total;

  $("resumo-nome").textContent = conjunto
    ? tituloDoConjunto(conjunto)
    : rotulo(dicionario.arquivo);

  // O caminho da partição é a procedência do número: de que entidade, de que
  // exercício, de que UF. Sem ele a página descreveria colunas no vazio.
  $("resumo-caminho").textContent = conjunto
    ? [
        conjunto.entidade,
        rotulo(conjunto.tipo_documento),
        conjunto.ano,
        conjunto.uf,
      ].join(" · ")
    : dicionario.arquivo;

  const abrir = new URLSearchParams({ arquivo: dicionario.arquivo });
  $("resumo-abrir").href = `visualizador.html?${abrir}`;

  const somaPreenchimento = colunas.reduce(
    (soma, coluna) => soma + (coluna.preenchimento || 0),
    0
  );
  const ressalvas = colunas.filter((coluna) =>
    eRessalva(diagnosticar(coluna, total))
  ).length;

  $("resumo-linhas").textContent = numero(total);
  $("resumo-colunas").textContent = numero(colunas.length);
  $("resumo-preenchimento").textContent = colunas.length
    ? porcento(somaPreenchimento / colunas.length)
    : "—";
  $("resumo-alertas").textContent = numero(ressalvas);
  $("resumo-alertas").classList.toggle("resumo-valor-alerta", ressalvas > 0);

  desenharTipos(colunas);
}

// --------------------------------------------------------------------------
// A tabela de colunas
// --------------------------------------------------------------------------

function barraDePreenchimento(coluna) {
  const fracao = coluna.preenchimento || 0;
  const pontos = Math.round(fracao * 100);

  // Três faixas, e não um gradiente: o olho compara "cheia, furada, vazia"
  // muito mais rápido do que compara dois tons de verde.
  let faixa = "cheia";
  if (pontos === 0) faixa = "vazia";
  else if (fracao < LIMITE_PARCIAL) faixa = "critica";
  else if (pontos < 100) faixa = "parcial";

  return `
    <span class="preenchimento">
      <span class="preenchimento-barra ${faixa}" role="img"
            aria-label="${pontos}% preenchida">
        <span style="width:${pontos}%"></span>
      </span>
      <span class="preenchimento-numero">${pontos}%</span>
    </span>`;
}

function etiquetaDeTipo(tipo) {
  const cor = CORES_DE_TIPO[tipo] || "#94a3b8";
  return `<span class="tipo-etiqueta" style="--cor-tipo:${cor}">${escapar(tipo)}</span>`;
}

function linhaDeColuna(coluna, total) {
  const diagnostico = diagnosticar(coluna, total);

  const nota = diagnostico
    ? `<div class="coluna-nota grau-${diagnostico.grau}">
         <strong>${escapar(diagnostico.rotulo)}</strong>
         ${escapar(diagnostico.explicacao)}
       </div>`
    : "";

  // `nome` e `rotulo` divergem quando o CSV de origem trouxe BOM ou aspas no
  // cabeçalho. Quem for consultar o arquivo direto precisa do nome cru, então
  // ele aparece — mas em segundo plano, sob o rótulo que se lê.
  const nomeCru =
    coluna.nome !== coluna.rotulo
      ? `<span class="coluna-nome-cru" title="Nome da coluna dentro do arquivo">${escapar(coluna.nome)}</span>`
      : "";

  return `
    <tr data-diagnostico="${escapar(diagnostico?.chave || "")}"
        data-grau="${escapar(diagnostico?.grau || "")}"
        data-nome="${escapar(coluna.rotulo.toLowerCase())}">
      <td class="coluna-letra">${escapar(coluna.letra || "")}</td>
      <td class="coluna-identidade">
        <span class="coluna-rotulo">${escapar(coluna.rotulo)}</span>
        ${nomeCru}
        ${nota}
      </td>
      <td>${etiquetaDeTipo(coluna.tipo)}</td>
      <td class="coluna-preenchimento">${barraDePreenchimento(coluna)}</td>
      <td class="numero">${numero(coluna.preenchidas)}<span class="de-total"> de ${numero(total)}</span></td>
      <td class="numero">${numero(coluna.distintos)}</td>
    </tr>`;
}

function desenharColunas(dicionario) {
  const total = dicionario.total;

  $("colunas-conteudo").innerHTML = `
    <div class="table-responsive">
      <table class="dicionario-tabela">
        <thead>
          <tr>
            <th class="coluna-letra">Col.</th>
            <th>Coluna</th>
            <th>Tipo</th>
            <th>Preenchimento</th>
            <th class="numero">Linhas com valor</th>
            <th class="numero">Valores distintos</th>
          </tr>
        </thead>
        <tbody id="colunas-corpo">
          ${dicionario.colunas.map((c) => linhaDeColuna(c, total)).join("")}
        </tbody>
      </table>
    </div>
    <p class="colunas-vazio empty-state" id="colunas-vazio" hidden>
      Nenhuma coluna deste conjunto casa com o filtro.
    </p>`;
}

/** Esconde as linhas que não casam com a busca e com o diagnóstico escolhido.
 *
 * Filtra o DOM em vez de redesenhar a tabela: um conjunto do acervo tem
 * dezenas de colunas, não milhares, e manter as linhas montadas faz a busca
 * responder a cada tecla sem repintar tudo.
 */
function filtrarColunas() {
  const busca = $("colunas-busca").value.trim().toLowerCase();
  const escolha = $("colunas-diagnostico").value;
  const linhas = [...document.querySelectorAll("#colunas-corpo tr")];

  let visiveis = 0;

  for (const linha of linhas) {
    const casaBusca = !busca || linha.dataset.nome.includes(busca);
    const casaDiagnostico =
      !escolha ||
      (escolha === "alerta"
        ? linha.dataset.grau && linha.dataset.grau !== "nota"
        : linha.dataset.diagnostico === escolha);

    const mostra = casaBusca && casaDiagnostico;
    linha.hidden = !mostra;
    if (mostra) visiveis += 1;
  }

  $("colunas-vazio").hidden = visiveis > 0;
}

// --------------------------------------------------------------------------
// Abrir um conjunto
// --------------------------------------------------------------------------

async function abrirConjunto(arquivo) {
  if (!arquivo) return;

  const conjunto = descreverConjunto(arquivo);
  state.conjunto = conjunto || { arquivo };

  $("convite").hidden = true;
  $("painel").hidden = false;
  $("seletor").open = false;
  $("colunas-conteudo").innerHTML = `<p class="carregando">Medindo as colunas…</p>`;

  // A URL passa a apontar para este conjunto: assim ele pode ser mandado a
  // alguém, e o F5 não devolve a tela em branco.
  const url = new URL(location.href);
  url.searchParams.set("arquivo", arquivo);
  history.replaceState(null, "", url);

  let dicionario;
  try {
    dicionario = await pedir(
      "/api/v1/conjuntos/colunas",
      new URLSearchParams({ arquivo })
    );
  } catch {
    $("colunas-conteudo").innerHTML =
      `<p class="empty-state">Não foi possível medir as colunas deste conjunto.</p>`;
    return;
  }

  state.dicionario = dicionario;

  // A ressalva vem junto da medida, e não só do HTML: é o mesmo texto que
  // acompanha o `_dictionary.json` gravado ao lado do Parquet.
  if (dicionario.ressalva) {
    $("ressalva-texto").textContent = dicionario.ressalva;
  }

  desenharResumo(dicionario, conjunto);
  desenharColunas(dicionario);
  filtrarColunas();

  $("seletor-atual").textContent = conjunto
    ? tituloDoConjunto(conjunto)
    : rotulo(dicionario.arquivo);

  document.querySelectorAll(".conjunto-item").forEach((item) =>
    item.classList.toggle("aberto", item.dataset.arquivo === arquivo)
  );
}

// --------------------------------------------------------------------------
// Eventos e entrada
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

function ligarEventos() {
  const recarregar = () => {
    state.conjuntos.pagina = 1;
    comErro(carregarConjuntos);
  };

  $("filtro-busca").addEventListener("input", aoDigitar(recarregar));
  ["filtro-entidade", "filtro-ano", "filtro-uf", "filtro-ordem"].forEach((id) =>
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

  $("colunas-busca").addEventListener("input", aoDigitar(filtrarColunas, 150));
  $("colunas-diagnostico").addEventListener("change", filtrarColunas);
}

/** Entidades, exercícios e UFs do acervo, para os filtros do seletor. */
async function carregarFiltros() {
  const opcoes = await pedir("/api/v1/filtros", new URLSearchParams());

  const preencher = (id, valores, todos) => {
    $(id).innerHTML =
      `<option value="">${todos}</option>` +
      (valores || [])
        .map((v) => `<option value="${escapar(v)}">${escapar(v)}</option>`)
        .join("");
  };

  preencher("filtro-entidade", opcoes.entidades, "Todas");
  preencher("filtro-ano", opcoes.anos, "Todos");
  preencher("filtro-uf", opcoes.ufs, "Todas");
}

async function main() {
  ligarEventos();

  // Duas portas de entrada: `arquivo` abre um conjunto direto — é o link que
  // o visualizador e o catálogo mandam para cá —, e `busca` deixa o seletor
  // pré-filtrado.
  const parametros = new URLSearchParams(location.search);
  const arquivo = parametros.get("arquivo");
  const busca = parametros.get("busca");

  if (busca) $("filtro-busca").value = busca;
  await comErro(carregarFiltros);

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
      await abrirConjunto(conjuntos[0].arquivo);
    }
  });
}

main();
