// Tela inicial: explica o sistema e mede o acervo.
//
// O texto da página é fixo — é a explicação do fluxo, e ela não muda a cada
// coleta. O que muda são os quatro números do topo, e esses vêm da API. Se ela
// estiver fora do ar, a explicação continua lá: só os números ficam em "—".

import { $, API_BASE, numero, pedir } from "./catalogo.js";

const TEMA_DOCUMENTOS = "documentos";
const TEMA_PLANILHAS = "planilhas";

/** Quantas linhas o catálogo de um tema tem.
 *
 * Só o total interessa aqui, então pede a menor página possível: quem conta é
 * a API, sobre o tema todo, e não esta página sobre o que baixou.
 */
async function totalDoCatalogo(tema) {
  const params = new URLSearchParams({ tema, page: "1", page_size: "1" });
  const resposta = await pedir("/api/v1/documentos", params);
  return resposta.total || 0;
}

async function totalDeConjuntos() {
  const params = new URLSearchParams({ page: "1", page_size: "1" });
  const resposta = await pedir("/api/v1/conjuntos", params);
  return resposta.total || 0;
}

async function estatisticas() {
  return pedir("/api/v1/estatisticas", new URLSearchParams());
}

function mostrarErro(mensagem) {
  $("aviso-erro").hidden = false;
  $("aviso-erro-texto").textContent =
    `${mensagem} Confira se ela está no ar em ${API_BASE} e rode, na raiz do projeto:`;
  $("badge-api").textContent = "API fora do ar";
  $("total-recorte").textContent =
    "os números aparecem quando a API responder";
}

async function carregarNumeros() {
  // As quatro consultas são independentes: uma rodada, não quatro em fila.
  const [documentos, planilhas, conjuntos, geral] = await Promise.all([
    totalDoCatalogo(TEMA_DOCUMENTOS),
    totalDoCatalogo(TEMA_PLANILHAS),
    totalDeConjuntos(),
    estatisticas(),
  ]);

  $("total-documentos").textContent = numero(documentos);
  $("total-planilhas").textContent = numero(planilhas);
  $("total-conjuntos").textContent = numero(conjuntos);
  $("total-linhas").textContent = numero(geral.total_registros);

  // O recorte do acervo dito pelos próprios dados, não por um texto que
  // envelhece: quantos temas e quantas UFs as partições realmente têm.
  $("total-recorte").textContent =
    `em ${numero(geral.total_temas)} temas e ${numero(geral.total_ufs)} unidades da federação`;

  const vazio = !documentos && !planilhas && !conjuntos;
  $("badge-api").textContent = vazio ? "API sem acervo" : "API conectada";
  $("aviso-erro").hidden = true;
}

function init() {
  // A documentação interativa vive na API, não no portal: o endereço dela
  // acompanha o ?api=… que a página recebeu.
  $("link-api-docs").href = `${API_BASE}/docs`;

  carregarNumeros().catch((erro) => mostrarErro(erro.message));
}

init();
