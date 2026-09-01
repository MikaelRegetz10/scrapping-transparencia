# scrapers/abdi_pdf.py
"""Varredura das rotas de transparência da ABDI via navegador headless.

Cobre as duas rotas alvo:
  * /transparencia/aquisicao-de-bens-e-servicos/ -> grid JetEngine com accordions
    de licitação; cada accordion guarda os anexos em botões "jet-download".
  * /transparencia/processo-seletivo/            -> abas por ano com os PDFs dos
    comunicados de cada processo.

Por que navegador e não requests: a ABDI fica atrás de uma camada de proteção
que devolve uma página de espera para cliente sem JavaScript, e o grid de
aquisições é montado no cliente. Abrindo com o Chromium os dois problemas somem
de uma vez — é a mesma técnica usada em `scrapers/senar.py`.
"""

import logging
from typing import Dict, List
from urllib.parse import urljoin

from scrapers.base import BaseScraper
from scrapers.navegador import (
    abrir_pagina,
    abrir_sessoes,
    clicar_ate_esgotar,
    eh_documento,
    nome_arquivo,
    rotulo,
    separa_publicacao,
    tipo_arquivo,
)

logger = logging.getLogger("scrapers.abdi_pdf")

BASE_SITE = "https://www.abdi.com.br"
PATH_AQUISICOES = "/transparencia/aquisicao-de-bens-e-servicos/"
PATH_PROCESSO_SELETIVO = "/transparencia/processo-seletivo/"

# Botão "Carregar Mais" do grid de aquisições. O id sai do próprio `data-nav`
# que o JetEngine publica no HTML (campo `more_el`).
SELETOR_CARREGAR_MAIS = "#load-six"
SELETOR_ITEM_GRID = ".jet-listing-grid__item"

# Lê o grid já renderizado: para cada licitação, o título do accordion e os
# anexos, cada um com o rótulo do cabeçalho imediatamente anterior ("Edital",
# "Ata da Sessão"...). O cabeçalho que sobra sem anexo é a situação do processo.
_JS_AQUISICOES = """
() => {
  const limpa = t => (t || '').replace(/\\s+/g, ' ').trim().replace(/\\.$/, '');
  const saida = [];

  document.querySelectorAll('.jet-listing-grid__item').forEach(item => {
    const tituloEl = item.querySelector('.e-n-accordion-item-title-text');
    const licitacao = tituloEl ? limpa(tituloEl.innerText) : '';

    const anexos = [];
    let rotuloCorrente = '';
    let rotuloUsado = true;
    let situacao = '';

    item.querySelectorAll('*').forEach(el => {
      const classes = el.classList;
      if (classes && classes.contains('elementor-heading-title')) {
        if (!rotuloUsado && rotuloCorrente) situacao = rotuloCorrente;
        rotuloCorrente = limpa(el.innerText);
        rotuloUsado = false;
        return;
      }
      if (el.tagName === 'A' && classes && classes.contains('jet-download')) {
        const href = el.getAttribute('href');
        if (!href) return;
        rotuloUsado = true;
        let url;
        try { url = new URL(href, location.href).href; } catch (e) { return; }
        anexos.push({ url: url, documento: rotuloCorrente });
      }
    });

    if (!rotuloUsado && rotuloCorrente) situacao = rotuloCorrente;
    anexos.forEach(a => saida.push({
      licitacao: licitacao, documento: a.documento,
      url: a.url, situacao: situacao,
    }));
  });

  return saida;
}
"""


# Lê a página de processo seletivo já renderizada. Cada documento é uma linha
# com o título à esquerda (um h2) e o botão "Visualizar" à direita, agrupada sob
# a vaga (um h3) e dentro da aba do ano. O texto do link é sempre "Visualizar",
# então título, vaga e ano vêm do que está em volta.
_JS_PROCESSO_SELETIVO = """
() => {
  const limpa = t => (t || '').replace(/\\s+/g, ' ').trim();

  // Primeiro elemento que casa com o seletor entre os irmãos anteriores,
  // subindo a árvore a partir do link.
  const anterior = (a, seletor) => {
    let el = a;
    while (el && el !== document.body) {
      let irmao = el.previousElementSibling;
      while (irmao) {
        if (irmao.matches && irmao.matches(seletor)) {
          const t = limpa(irmao.innerText);
          if (t) return t;
        }
        const dentro = irmao.querySelector && irmao.querySelector(seletor);
        if (dentro) {
          const t = limpa(dentro.innerText);
          if (t) return t;
        }
        irmao = irmao.previousElementSibling;
      }
      el = el.parentElement;
    }
    return '';
  };

  // O painel da aba tem o id do gatilho + "-tab"; é assim que se chega ao ano.
  const abaDe = (a) => {
    const painel = a.closest('.eael-tab-content-item');
    if (!painel || !painel.id) return '';
    const gatilho = document.getElementById(painel.id.replace(/-tab$/, ''));
    return gatilho ? limpa(gatilho.innerText) : '';
  };

  // O banner de consentimento fica fora do conteúdo da página e também linka
  // PDF (a política de privacidade). Sem esse corte ele entra na coleta com o
  // título de qualquer cabeçalho que estiver por perto.
  const CONSENTIMENTO = '[class*="adopt-"], [id*="cookie" i], [class*="cookie" i]';

  const saida = [];
  document.querySelectorAll('a[href]').forEach(a => {
    const bruto = a.getAttribute('href');
    if (!bruto || bruto.startsWith('#') || bruto.startsWith('javascript:')
        || bruto.startsWith('mailto:')) return;
    if (a.closest(CONSENTIMENTO)) return;
    let url;
    try { url = new URL(bruto, location.href).href; } catch (e) { return; }

    saida.push({
      url: url,
      documento: anterior(a, 'h2, .elementor-heading-title').slice(0, 300),
      vaga: anterior(a, 'h3').slice(0, 200),
      aba: abaDe(a).slice(0, 80),
      texto: limpa(a.innerText).slice(0, 200),
    });
  });

  return saida;
}
"""


def _trunca(texto: str, limite: int) -> str:
    return texto if len(texto) <= limite else texto[: limite - 1].rstrip() + "…"


def _titulo_licitacao(licitacao: str, documento: str) -> str:
    """Junta licitação + documento sem repetir e sem virar um título quilométrico.

    O nome do documento fica no fim e nunca é truncado: é ele que diferencia o
    edital da ata dentro de uma mesma licitação.
    """
    if not licitacao:
        return _trunca(documento or "Documento", 200)
    if not documento or licitacao.startswith(documento) or documento.startswith(licitacao):
        maior = licitacao if len(licitacao) >= len(documento) else documento
        return _trunca(maior, 200)
    return f"{_trunca(licitacao, 120)} — {documento}"


class ABDIPdfScraper(BaseScraper):
    """Coleta os links de documentos das rotas de transparência da ABDI.

    Args:
        aquisicoes: varre /transparencia/aquisicao-de-bens-e-servicos/.
        processo_seletivo: varre /transparencia/processo-seletivo/.
        apenas_pdf: mantém só PDFs (padrão). Com False registra também csv,
            xlsx, docx e afins encontrados nas mesmas páginas.
        max_cliques: teto de cliques no "Carregar Mais" do grid de aquisições.
        headless: False abre o Chromium com janela, útil para depurar.
    """

    def __init__(
        self,
        aquisicoes: bool = True,
        processo_seletivo: bool = True,
        apenas_pdf: bool = True,
        max_cliques: int = 40,
        headless: bool = True,
    ):
        super().__init__(
            name="ABDI_PDF",
            base_url=urljoin(BASE_SITE, "/transparencia/"),
            routes={
                "Aquisição de Bens e Serviços": urljoin(BASE_SITE, PATH_AQUISICOES),
                "Processo Seletivo": urljoin(BASE_SITE, PATH_PROCESSO_SELETIVO),
            },
        )
        self.aquisicoes = aquisicoes
        self.processo_seletivo = processo_seletivo
        self.apenas_pdf = apenas_pdf
        self.max_cliques = max_cliques
        self.headless = headless

    # ------------------------------------------------------------------
    # Contrato do BaseScraper
    # ------------------------------------------------------------------
    def extract_links(self) -> List[Dict[str, str]]:
        registros: List[Dict[str, str]] = []
        vistos: set[str] = set()
        falhas: List[str] = []

        with abrir_sessoes(headless=self.headless) as nova_sessao:
            rotas = [
                (self.aquisicoes, "Aquisição de Bens e Serviços", self._extrai_aquisicoes),
                (self.processo_seletivo, "Processo Seletivo", self._extrai_processo_seletivo),
            ]

            for habilitada, secao, extrator in rotas:
                if not habilitada:
                    continue

                # Sessão limpa por rota: o 403 da paginação de aquisições marca
                # a sessão e faria a rota seguinte cair na página de espera.
                try:
                    encontrados = extrator(nova_sessao())
                except Exception as e:
                    falhas.append(f"{secao}: {e}")
                    logger.error("Falha ao varrer '%s': %s", secao, e)
                    continue

                # A deduplicação precisa valer também dentro da mesma rota: a
                # página repete o mesmo link em mais de um lugar.
                novos = []
                for registro in encontrados:
                    if registro["download_url"] in vistos:
                        continue
                    vistos.add(registro["download_url"])
                    registro.setdefault("section", secao)
                    novos.append(registro)

                registros.extend(novos)
                logger.info("📑 %s: %d documento(s).", secao, len(novos))

        if not registros and falhas:
            raise RuntimeError(
                "Nenhuma rota da ABDI pôde ser lida. Detalhes: " + " | ".join(falhas)
            )

        return registros

    def _registro(self, titulo: str, contexto: str, url: str) -> Dict[str, str]:
        titulo_limpo, publicado_em = separa_publicacao(titulo)
        return {
            "source": self.name,
            "title": titulo_limpo or "Título não identificado",
            "context": contexto,
            "download_url": url,
            "file_name": nome_arquivo(url),
            "file_type": tipo_arquivo(url),
            "published_at": publicado_em,
        }

    # ------------------------------------------------------------------
    # Rota 1: Aquisição de Bens e Serviços
    # ------------------------------------------------------------------
    def _extrai_aquisicoes(self, page) -> List[Dict[str, str]]:
        url = urljoin(BASE_SITE, PATH_AQUISICOES)
        if not abrir_pagina(page, url):
            raise RuntimeError("o portal não liberou a página de aquisições")

        # O "Carregar Mais" busca as páginas seguintes por POST na própria rota.
        # Quando a proteção do site recusa esse POST a lista simplesmente para
        # de crescer — sem o aviso abaixo isso passa por "acabaram as licitações".
        recusas: List[int] = []
        registrar = lambda r: (  # noqa: E731 - listener curto de diagnóstico
            recusas.append(r.status)
            if r.request.method == "POST" and r.status >= 400 and PATH_AQUISICOES in r.url
            else None
        )
        page.on("response", registrar)
        try:
            total = clicar_ate_esgotar(
                page,
                seletor_botao=SELETOR_CARREGAR_MAIS,
                seletor_itens=SELETOR_ITEM_GRID,
                max_cliques=self.max_cliques,
            )
        finally:
            page.remove_listener("response", registrar)

        if recusas:
            logger.warning(
                "A ABDI recusou a paginação do grid (HTTP %s). O bloqueio é da "
                "proteção do portal e vale para a sessão inteira — por isso cada "
                "rota abre a sua.",
                ", ".join(str(s) for s in sorted(set(recusas))),
            )

        lidas, publicadas = self._paginas_do_grid(page)
        if publicadas > lidas:
            logger.warning(
                "Cobertura parcial das aquisições: %d de %d página(s) do grid, "
                "%d licitação(ões) lida(s). O resto só sai se o portal liberar a "
                "paginação para este cliente.",
                lidas, publicadas, total,
            )
        else:
            logger.info("Grid de aquisições com %d licitação(ões) carregada(s).", total)

        registros = []
        for anexo in page.evaluate(_JS_AQUISICOES):
            if not eh_documento(anexo["url"], self.apenas_pdf):
                continue

            contexto = "Aquisição de Bens e Serviços"
            if anexo.get("situacao"):
                contexto += f" | Situação: {anexo['situacao']}"

            registros.append(
                self._registro(
                    _titulo_licitacao(anexo["licitacao"], anexo["documento"]),
                    contexto,
                    anexo["url"],
                )
            )

        return registros

    @staticmethod
    def _paginas_do_grid(page) -> tuple[int, int]:
        """(páginas lidas, páginas publicadas) segundo o próprio grid.

        O JetEngine anuncia as duas em `data-page` e `data-pages`. É como saber
        o tamanho do que ficou de fora quando a paginação é recusada — sem isso
        a coleta parcial passa por completa.
        """
        dados = page.evaluate(
            """() => {
                const g = document.querySelector('[data-pages]');
                return g ? {lidas: g.getAttribute('data-page'),
                            publicadas: g.getAttribute('data-pages')} : null;
            }"""
        )
        if not dados:
            return 0, 0

        def inteiro(valor) -> int:
            try:
                return int(valor)
            except (TypeError, ValueError):
                return 0

        return inteiro(dados["lidas"]), inteiro(dados["publicadas"])

    # ------------------------------------------------------------------
    # Rota 2: Processo Seletivo
    # ------------------------------------------------------------------
    def _extrai_processo_seletivo(self, page) -> List[Dict[str, str]]:
        """Os comunicados de todos os anos já vêm no HTML.

        As abas por ano ("Processos Seletivos 2024", 2023...) são trocadas só no
        CSS, então basta ler a página inteira uma vez: não há requisição nova
        por trás de cada aba.

        O título de cada documento é o texto que aparece ao lado do botão — o
        link em si só diz "Visualizar". Junto dele ficam registrados a vaga que
        encabeça o bloco e o ano da aba, que são o que distingue dois
        comunicados de mesmo nome ("Comunicado 4 - Resultado da triagem
        curricular" se repete em toda vaga do processo).
        """
        url = urljoin(BASE_SITE, PATH_PROCESSO_SELETIVO)
        if not abrir_pagina(page, url):
            raise RuntimeError("o portal não liberou a página de processo seletivo")

        registros = []
        sem_titulo = 0

        for anexo in page.evaluate(_JS_PROCESSO_SELETIVO):
            if not eh_documento(anexo["url"], self.apenas_pdf):
                continue

            titulo = anexo["documento"]
            if not titulo:
                # Sem o cabeçalho da linha sobra o texto do link, que costuma
                # ser "Visualizar"; aí o nome do arquivo identifica melhor.
                sem_titulo += 1
                titulo = rotulo(
                    {"texto": anexo["texto"]}, reserva=nome_arquivo(anexo["url"])
                )

            contexto = " | ".join(
                parte
                for parte in ("Processo Seletivo", anexo["aba"], anexo["vaga"])
                if parte
            )

            registro = self._registro(titulo, contexto, anexo["url"])
            if anexo["aba"]:
                registro["section"] = f"Processo Seletivo — {anexo['aba']}"
            registros.append(registro)

        if sem_titulo:
            logger.warning(
                "%d documento(s) de processo seletivo sem título na página: "
                "ficaram com o texto do próprio link.", sem_titulo,
            )

        return registros
