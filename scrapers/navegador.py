# scrapers/navegador.py
"""Infraestrutura Playwright compartilhada pelos scrapers de documentos.

Mesma abordagem adotada em `scrapers/senar.py`: em vez de baixar o HTML com
requests, um Chromium headless abre a página, executa o JavaScript e só então
lemos o DOM já montado. É o que permite ler portais que constroem a listagem no
cliente (JetEngine da ABDI, abas Alpine do Portal da Indústria) e atravessar
páginas que só liberam o conteúdo depois de rodar JS.

Requer o navegador instalado uma única vez:

    python -m playwright install chromium
"""

from contextlib import contextmanager
import logging
import os
import time
from typing import Iterator, Optional
from urllib.parse import urlparse

from playwright.sync_api import Page, sync_playwright

logger = logging.getLogger("scrapers.navegador")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# Títulos que a página de intersticial exibe enquanto o JS de verificação roda.
# Enquanto um deles estiver no <title> o conteúdo real ainda não chegou.
TITULOS_DE_ESPERA = (
    "just a moment",
    "um momento",
    "checking your browser",
    "verificando seu navegador",
    "attention required",
)

EXTENSOES_DOCUMENTO = {
    "pdf", "csv", "xlsx", "xls", "ods", "json", "zip", "doc", "docx", "txt", "xml"
}


@contextmanager
def abrir_navegador(headless: bool = True) -> Iterator[Page]:
    """Sobe um Chromium headless e entrega a página pronta para navegar.

    Os args `--no-sandbox` são os mesmos usados no scraper do SENAR: sem eles o
    Chromium não sobe em container nem em algumas distros Linux.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            args=["--no-sandbox", "--disable-setuid-sandbox"],
        )
        context = browser.new_context(
            ignore_https_errors=True,
            locale="pt-BR",
            user_agent=USER_AGENT,
        )
        page = context.new_page()
        try:
            yield page
        finally:
            browser.close()


def esta_no_intersticial(page: Page) -> bool:
    try:
        titulo = (page.title() or "").lower()
    except Exception:
        return False
    return any(marca in titulo for marca in TITULOS_DE_ESPERA)


def abrir_pagina(
    page: Page,
    url: str,
    espera_ms: int = 4000,
    tentativas: int = 3,
    paciencia_s: int = 40,
    timeout_ms: int = 60000,
) -> bool:
    """Navega até `url` e espera o conteúdo real aparecer.

    Alguns portais (a ABDI, por exemplo) ficam atrás de uma camada que devolve
    uma página de espera e só entrega o HTML depois que o JS roda. Aqui a única
    estratégia é a mesma de uma pessoa no navegador: aguardar e, se não liberar,
    recarregar. Nada de contornar verificação — se continuar barrado devolvemos
    False e quem chamou registra a rota como não lida.
    """
    for tentativa in range(1, tentativas + 1):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        except Exception as e:
            logger.warning("Falha ao abrir %s (tentativa %d): %s", url, tentativa, e)
            continue

        page.wait_for_timeout(espera_ms)

        limite = time.time() + paciencia_s
        while esta_no_intersticial(page) and time.time() < limite:
            page.wait_for_timeout(3000)

        if not esta_no_intersticial(page):
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass  # networkidle nem sempre chega em página com polling
            return True

        logger.warning(
            "Página de espera ainda ativa em %s (tentativa %d/%d).",
            url, tentativa, tentativas,
        )

    logger.error(
        "O portal não liberou o conteúdo de %s após %d tentativas — "
        "a rota ficou sem leitura nesta execução.", url, tentativas,
    )
    return False


def tipo_arquivo(url: str) -> str:
    """Deduz o formato pelo caminho da URL."""
    # O JetEngine da ABDI serve os anexos por querystring, sem extensão.
    if "jet_download=" in url:
        return "pdf"

    ext = os.path.splitext(urlparse(url).path)[1].lower().lstrip(".")
    if ext in EXTENSOES_DOCUMENTO:
        return ext
    return ext or "link/página"


def eh_documento(url: str, apenas_pdf: bool) -> bool:
    tipo = tipo_arquivo(url)
    return tipo == "pdf" if apenas_pdf else tipo in EXTENSOES_DOCUMENTO


# JS injetado nas páginas para ler as âncoras já com o rótulo mais próximo.
# Subir a árvore procurando o cabeçalho anterior é o que dá nome ao documento:
# o texto do link costuma ser só "Visualizar" ou "Download".
_JS_ANCORAS = """
() => {
  const limpa = t => (t || '').replace(/\\s+/g, ' ').trim();
  const CABECALHOS = 'h1,h2,h3,h4,h5,h6,.elementor-heading-title,'
    + '.e-n-accordion-item-title-text,.si-download-titulo,strong,b';

  const cabecalhoDe = (a) => {
    let el = a;
    while (el && el !== document.body) {
      let irmao = el.previousElementSibling;
      while (irmao) {
        if (irmao.matches && irmao.matches(CABECALHOS)) {
          const t = limpa(irmao.innerText);
          if (t) return t;
        }
        const dentro = irmao.querySelector && irmao.querySelector(CABECALHOS);
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

  const saida = [];
  document.querySelectorAll('a[href]').forEach(a => {
    // Âncoras dentro de SVG têm href como SVGAnimatedString, não string.
    const bruto = a.getAttribute('href');
    if (!bruto || bruto.startsWith('#') || bruto.startsWith('javascript:')
        || bruto.startsWith('mailto:')) return;
    let url;
    try { url = new URL(bruto, location.href).href; } catch (e) { return; }
    saida.push({
      url: url,
      texto: limpa(a.innerText).slice(0, 200),
      cabecalho: cabecalhoDe(a).slice(0, 250),
      titulo_attr: limpa(a.getAttribute('title') || '').slice(0, 200),
    });
  });
  return saida;
}
"""


def ancoras_da_pagina(page: Page) -> list[dict]:
    """Todas as âncoras da página com o cabeçalho mais próximo como rótulo."""
    try:
        return page.evaluate(_JS_ANCORAS)
    except Exception as e:
        logger.warning("Falha ao ler as âncoras da página: %s", e)
        return []


def rotulo(ancora: dict, reserva: str = "") -> str:
    """Escolhe o melhor nome disponível para o documento.

    Textos genéricos de botão ("Visualizar", "Download") não identificam nada,
    então nesses casos o cabeçalho da seção vale mais.
    """
    generico = {"visualizar", "download", "baixar", "acessar", "ver", "abrir", ""}
    texto = (ancora.get("texto") or "").strip()
    cabecalho = (ancora.get("cabecalho") or "").strip()

    if texto.lower() not in generico:
        return texto
    return cabecalho or ancora.get("titulo_attr") or reserva or "Documento"


def clicar_ate_esgotar(
    page: Page,
    seletor_botao: str,
    seletor_itens: str,
    max_cliques: int = 30,
    espera_ms: int = 3500,
) -> int:
    """Clica no "Carregar Mais" até a lista parar de crescer.

    Devolve o total de itens carregados. Para quando o botão some, quando o
    clique falha ou quando uma rodada não traz item novo — o que também cobre o
    caso de o portal recusar a requisição de paginação.
    """
    botao = page.locator(seletor_botao).first
    total = page.locator(seletor_itens).count()

    for clique in range(1, max_cliques + 1):
        if botao.count() == 0 or not botao.is_visible():
            break

        try:
            botao.scroll_into_view_if_needed(timeout=5000)
            page.wait_for_timeout(400)
            botao.click(timeout=8000)
        except Exception as e:
            logger.info("Fim da paginação no clique %d: %s", clique, type(e).__name__)
            break

        page.wait_for_timeout(espera_ms)
        novo_total = page.locator(seletor_itens).count()
        if novo_total <= total:
            logger.info(
                "Clique %d não trouxe itens novos (%d): fim da lista.", clique, total
            )
            break

        total = novo_total
        logger.debug("Clique %d: %d itens carregados.", clique, total)

    return total


def texto_opcional(page: Page, seletor: str) -> Optional[str]:
    """Texto do primeiro elemento que casar com o seletor, ou None."""
    loc = page.locator(seletor)
    if loc.count() == 0:
        return None
    try:
        return loc.first.inner_text().strip()
    except Exception:
        return None
