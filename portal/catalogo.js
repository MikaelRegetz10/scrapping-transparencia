// Peças comuns às páginas de catálogo (documentos e planilhas).
//
// As duas listam links coletados dos portais de transparência e conversam com
// a mesma API; o que muda é o tema consultado e o que cada item mostra. Tudo
// que é igual nas duas mora aqui, para não haver duas versões da escapatória
// de HTML — a que protege o portal do texto raspado de terceiros.

// Endereço da API. Em produção o portal costuma ser servido pela mesma origem;
// no desenvolvimento a API sobe à parte, daí o localhost:8000 como padrão.
// Dá para apontar para outro host sem editar o arquivo: a página aceita ?api=…
export const API_BASE = (
  new URLSearchParams(location.search).get("api") ||
  window.PORTAL_API_BASE ||
  "http://localhost:8000"
).replace(/\/$/, "");

// --------------------------------------------------------------------------
// Segurança: título, contexto e nome de arquivo vêm de páginas raspadas de
// terceiros. Nada disso entra no DOM sem passar por aqui.
// --------------------------------------------------------------------------

export function escapar(valor) {
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
export function urlSegura(valor) {
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
// Conversa com a API
// --------------------------------------------------------------------------

export async function pedir(caminho, params) {
  const resposta = await fetch(`${API_BASE}${caminho}?${params}`);
  if (!resposta.ok) {
    throw new Error(`A API respondeu ${resposta.status} em ${caminho}.`);
  }
  return resposta.json();
}

/** Quantas linhas há em cada valor de uma coluna, sob os filtros dados.
 *
 * Devolve um Map valor→total, já ordenado do maior para o menor pela API. É o
 * que monta os filtros: uma consulta por dimensão, não uma por opção.
 */
export async function contagens(coluna, filtros = {}) {
  const params = new URLSearchParams({ ...filtros, por: coluna });
  const resposta = await pedir("/api/v1/documentos/contagens", params);
  return new Map(
    (resposta.contagens || []).map(({ valor, total }) => [valor, total])
  );
}

// --------------------------------------------------------------------------
// Utilidades de interface
// --------------------------------------------------------------------------

export const $ = (id) => document.getElementById(id);

/** Adia a ação até a pessoa parar de digitar, para não consultar a cada tecla. */
export function aoDigitar(callback, espera = 350) {
  let temporizador;
  return () => {
    clearTimeout(temporizador);
    temporizador = setTimeout(callback, espera);
  };
}

export function selecionados(id) {
  return [...$(id).selectedOptions].map((opcao) => opcao.value);
}

export function preencherSelect(id, valores) {
  $(id).innerHTML = valores
    .map((valor) => `<option value="${escapar(valor)}">${escapar(valor)}</option>`)
    .join("");
}

export function numero(valor) {
  return Number(valor || 0).toLocaleString("pt-BR");
}
