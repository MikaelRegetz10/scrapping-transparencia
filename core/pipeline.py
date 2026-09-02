# core/pipeline.py
from collections import defaultdict
from datetime import datetime
import os
import re
import time
import pandas as pd
import requests
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE

from core.config import Config
from core.parquet_exporter import (
    TIPOS_CONHECIDOS,
    export_to_parquet,
    inferir_tipo_documento,
    remover_acentos,
    sanitize_name,
)
from core.profiler import analyze_dataset_quality
from core.validator import DEFAULT_HEADERS, check_url_status
from scrapers.base import BaseScraper


# Formatos que o core/profiler.py sabe perfilar. Os demais (zip, doc, docx...)
# são auditados só quanto à disponibilidade, sem baixar o corpo do arquivo.
PROFILABLE_TYPES = {"csv", "xlsx", "xls", "json"}

# Linhas de amostra guardadas por dataset aprovado no profiling.
ROWS_PER_SAMPLE = 20


def limpa_caracteres_ilegais(valor):
    """Remove os caracteres de controle ASCII que o formato XLSX recusa.

    Texto vindo dos portais traz \x00-\x08 e afins com frequência; sem esta
    limpeza o openpyxl levanta IllegalCharacterError e o relatório inteiro
    deixa de ser gravado por causa de uma célula.
    """
    if isinstance(valor, str):
        return ILLEGAL_CHARACTERS_RE.sub("", valor)
    return valor


def sanitize_sheet_name(title: str, index: int) -> str:
    """Higieniza o título para criar abas válidas no Excel."""
    clean_title = re.sub(r"[\\/*?:\[\]]", "", title)
    short_title = clean_title[:24].strip()
    return f"{index:02d}_{short_title}" if short_title else f"Aba_{index:02d}"


# Tema reservado aos links de documento. A API devolve documentos e linhas de
# planilha pelo mesmo endpoint, e ambos usam tipo_documento=contratos,
# licitacoes etc. Sem um tema próprio o portal teria de separar os dois no
# cliente, e aí o `total` da resposta — que é contado no banco, antes da
# filtragem — deixaria a paginação errada. A seção de origem não se perde:
# continua na coluna `secao_rota` de cada registro.
TEMA_DOCUMENTOS = "documentos"

# Tema reservado ao catálogo das planilhas. O ramo tabular já exporta o
# conteúdo de cada dataset — cada um no seu tema ("dados_abertos",
# "administracao_regional_…") —, mas o inventário dos arquivos em si (link,
# tamanho, se abriu, o que o profiler reclamou) só existia no Excel de
# qualidade. Ele mora aqui, separado do conteúdo, pela mesma razão que
# `documentos`: o `total` da API é contado no banco, antes de o cliente
# filtrar, e misturar catálogo com linha de dado quebraria a paginação.
TEMA_PLANILHAS = "planilhas"

# Processo seletivo é o grupo mais volumoso da ABDI e não cabe em nenhuma das
# categorias do `inferir_tipo_documento`. Fica aqui, e não lá, porque aquela
# função também classifica os datasets tabulares: mexer no vocabulário dela
# reclassificaria partições que já existem.
TIPO_PROCESSO_SELETIVO = "processos_seletivos"

# Vocabulário fechado dos documentos. O portal espelha esta lista para montar
# o filtro de tipo (ver portal/documentos.js, TIPOS_DE_DOCUMENTO).
TIPOS_DE_DOCUMENTO = frozenset(TIPOS_CONHECIDOS | {TIPO_PROCESSO_SELETIVO})


def tipo_do_documento(item: dict, title: str, section: str) -> str:
    """Categoria de um documento, preferindo a seção de origem ao título.

    A seção é a rota do portal em que o link foi encontrado ("Licitações",
    "Demonstrações Contábeis") e descreve o documento melhor que o título. Um
    comunicado de processo seletivo chamado "... - Contratos" trata da vaga na
    área de contratos; classificá-lo pelo título o transformaria num contrato.

    Sem categoria reconhecida o documento vira "outros" — nunca uma categoria
    própria. O título de um PDF é quase único, e deixá-lo virar tipo_documento
    criaria uma partição por arquivo e um filtro impossível de usar no portal.
    O título continua inteiro na coluna `titulo`.
    """
    for texto in (item.get("tcu_tipo_documento"), section, title):
        if not texto:
            continue

        if "seletivo" in remover_acentos(str(texto).lower()):
            return TIPO_PROCESSO_SELETIVO

        tipo = inferir_tipo_documento(texto)
        if tipo in TIPOS_CONHECIDOS and tipo != "outros":
            return tipo

    return "outros"


def particao_do_link(
    item: dict, title: str, section: str, config: Config, tema: str
) -> tuple:
    """Chave Hive (tema, tipo_documento, ano, uf) de um link catalogado.

    Devolve os valores já sanitizados, iguais aos que o `export_to_parquet`
    calcularia: assim dois links que caem na mesma pasta caem também no mesmo
    grupo, em vez de gerarem dois arquivos que se sobrescrevem.

    Serve aos dois catálogos — o de PDF e o de planilha —, que se distinguem
    apenas pelo `tema`. O tipo sai do mesmo vocabulário fechado nos dois
    casos: uma planilha de contratos e um contrato em PDF são a mesma
    categoria vista em formatos diferentes, e o portal filtra por ela igual.
    """
    return (
        tema,
        tipo_do_documento(item, title, section),
        str(item.get("tcu_ano") or config.ano),
        sanitize_name(item.get("tcu_uf") or "DN").upper(),
    )


def sim_ou_nao(valor) -> str:
    """Normaliza o `ativo`, que chega bool do ramo tabular e "SIM"/"NÃO" do PDF.

    O Excel de qualidade preserva cada um como veio; o Parquet não pode, senão
    o portal precisaria testar as duas formas para saber se um link caiu.
    """
    if isinstance(valor, str):
        return valor
    return "SIM" if valor else "NÃO"


def numera_status(df: pd.DataFrame) -> pd.DataFrame:
    """Deixa `status_http` inteiro, trocando por nulo o "ERRO" da verificação.

    Quando a requisição sequer completa, o laço grava a string "ERRO" no lugar
    do código HTTP. Numa partição em que ela apareça depois de algumas linhas
    numéricas o pyarrow já terá inferido int64 e recusa o arquivo inteiro — a
    partição some sem que nada além de um log denuncie. O aviso não se perde:
    a linha continua com `ativo="NÃO"` e com o motivo em `erros_qualidade`.
    """
    if "status_http" in df.columns:
        df["status_http"] = pd.to_numeric(
            df["status_http"], errors="coerce"
        ).astype("Int64")
    return df


def exporta_catalogo_para_parquet(
    registros: list,
    particoes: list,
    entidade: str,
    config: Config,
    logger,
    especie: str,
) -> int:
    """Grava um catálogo de links no Parquet Hive que a API de consulta lê.

    São dois catálogos, distinguidos pela `especie` — "documentos" para os PDF,
    "planilhas" para os arquivos tabulares. Em ambos o dado útil é o próprio
    link, e não o conteúdo: o PDF não tem o que perfilar, e o conteúdo da
    planilha já sai daqui por outro caminho, dataset a dataset. Sem esta
    gravação os dois inventários ficariam só no Excel, fora do alcance do
    portal.

    Os registros vão em lote, agrupados por partição, para não criar um
    arquivo Parquet por link.
    """
    if not registros:
        return 0

    grupos = defaultdict(list)
    for registro, chave in zip(registros, particoes):
        grupos[chave].append({**registro, "ativo": sim_ou_nao(registro.get("ativo"))})

    arquivos = 0
    for (tema, tipo_documento, ano, uf), linhas in grupos.items():
        caminho = export_to_parquet(
            df=numera_status(pd.DataFrame(linhas)),
            entidade=entidade,
            base_dir=config.output_dir,
            tema=tema,
            tipo_documento=tipo_documento,
            ano=ano,
            uf=uf,
            prefixo_nome=f"{entidade}_{especie}",
        )
        if caminho:
            arquivos += 1

    logger.info(
        f"📄 [{entidade}] {len(registros)} link(s) de {especie} exportado(s) para "
        f"Parquet em {arquivos} partição(ões)."
    )
    return arquivos


def run_scraper_pipeline(
    scraper: BaseScraper, config: Config, logger
) -> pd.DataFrame:
    """Executa o scraping, valida os links, exporta Parquet e gera o Excel."""
    logger.info(
        f"Iniciando Auditoria Multi-Rotas: {scraper.name} (Exercício: {config.ano})"
    )

    raw_items = scraper.extract_links()
    total = len(raw_items)
    logger.info(
        f"[{scraper.name}] Total de links extraídos de todas as rotas: {total}"
    )

    summary_tables = []
    summary_pdfs = []
    # Partição Hive de cada link, na mesma ordem do `summary_` correspondente.
    # É calculada dentro do laço porque depende do item bruto — que não
    # sobrevive a ele —, mas só é usada no fim.
    particoes_pdfs = []
    particoes_tabelas = []
    structured_samples = {}
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    table_idx = 1
    pdf_idx = 1

    for item in raw_items:
        url = item["download_url"]
        title = item["title"]
        file_type = item["file_type"]
        section = item.get("section", "Geral")

        if config.log_detalhado:
            logger.debug(
                f"[{section}] Verificando: {title[:40]} ({file_type.upper()})"
            )

        # Check Conectividade
        status_info = check_url_status(url)
        is_active = status_info["is_active"]
        status_code = status_info["status_code"] or "ERRO"
        size_kb = status_info["content_length_kb"]

        if not is_active:
            logger.warning(f"❌ [{section}] Link inativo (HTTP {status_code}): {title[:60]}")

        # ==========================================
        # 1. TRATAMENTO PARA DOCUMENTOS PDF
        # ==========================================
        if file_type == "pdf":
            summary_pdfs.append({
                "id": pdf_idx,
                "fonte": item["source"],
                "secao_rota": section,
                "titulo": title,
                "publicado_em": item.get("published_at", ""),
                "contexto": item.get("context", ""),
                "tipo_arquivo": "PDF",
                "nome_arquivo": item.get("file_name", ""),
                "url_download": url,
                "status_http": status_code,
                "ativo": "SIM" if is_active else "NÃO",
                "tamanho_kb": size_kb,
                "verificado_em": timestamp,
            })
            particoes_pdfs.append(
                particao_do_link(item, title, section, config, TEMA_DOCUMENTOS)
            )
            pdf_idx += 1

        # ==========================================
        # 2. TRATAMENTO PARA TABELAS (CSV, XLSX, JSON)
        # ==========================================
        else:

            if config.max_planilhas and (table_idx > config.max_planilhas):
                logger.info(
                    f"[Limite atingido] Interrompendo a leitura de tabelas após atingir "
                    f"o máximo configurado ({config.max_planilhas} planilhas)."
                )
                break

            is_structured = False
            erros_str = ""
            avisos_str = ""

            if is_active and file_type not in PROFILABLE_TYPES:
                # Formatos que o profiler não lê (zip, docx, ods...): auditamos
                # só a disponibilidade em vez de baixar o arquivo à toa.
                erros_str = (
                    f"Formato '{file_type}' fora do escopo do profiler: "
                    "link auditado apenas quanto à disponibilidade."
                )
                if config.log_detalhado:
                    logger.debug(
                        f"⏭️  Formato '{file_type}' não perfilável (download ignorado)."
                    )
            elif is_active:
                try:
                    res = requests.get(
                        url, headers=DEFAULT_HEADERS, timeout=25
                    )
                    profiling = analyze_dataset_quality(res.content, file_type)
                    is_structured = profiling["is_structured"]

                    erros_str = (
                        " | ".join(profiling["errors"])
                        if profiling["errors"]
                        else "Nenhum"
                    )
                    avisos_str = (
                        " | ".join(profiling["warnings"])
                        if profiling["warnings"]
                        else "Nenhum"
                    )

                    if is_structured:
                        df_valid = profiling["df_valid"]
                        sheet_name = sanitize_sheet_name(title, table_idx)
                        structured_samples[sheet_name] = df_valid.head(
                            ROWS_PER_SAMPLE
                        )
                        logger.info(
                            f"✅ [{section}] Dado estruturado. Amostra na aba '{sheet_name}'."
                        )

                        # ==========================================
                        # EXPORTAÇÃO PARQUET HIVE (TEMA / TIPO_DOC / ANO / UF)
                        # ==========================================
                        export_to_parquet(
                            df=df_valid,
                            entidade=scraper.name,
                            base_dir=config.output_dir,
                            tema=item.get("tcu_tema") or section,
                            tipo_documento=(
                                item.get("tcu_tipo_documento")
                                or item.get("tipo_documento")
                                or title
                            ),
                            ano=item.get("tcu_ano") or config.ano,
                            uf=item.get("tcu_uf") or "DN",
                            prefixo_nome=f"{item.get('source', 'extracao')}_{title}",
                        )
                    elif config.log_detalhado:
                        for err in profiling["errors"]:
                            logger.debug(f"   - {err}")
                except Exception as e:
                    erros_str = f"Falha de processamento: {e}"
                    logger.warning(f"❌ [{section}] Erro ao processar {title[:40]}: {e}")
            else:
                erros_str = f"Link inativo (HTTP {status_code})"

            summary_tables.append({
                "id": table_idx,
                "fonte": item["source"],
                "secao_rota": section,
                "titulo": title,
                "publicado_em": item.get("published_at", ""),
                "contexto": item.get("context", ""),
                "tipo_arquivo": file_type,
                "nome_arquivo": item.get("file_name", ""),
                "url_download": url,
                "status_http": status_code,
                "ativo": is_active,
                "content_type": status_info.get("content_type") or "",
                "tamanho_kb": size_kb,
                "estruturado": "SIM" if is_structured else "NÃO",
                "erros_qualidade": erros_str,
                "avisos_qualidade": avisos_str,
                "verificado_em": timestamp,
            })
            particoes_tabelas.append(
                particao_do_link(item, title, section, config, TEMA_PLANILHAS)
            )
            table_idx += 1

        if config.delay_entre_requisicoes > 0:
            time.sleep(config.delay_entre_requisicoes)

    # ==========================================
    # EXPORTAÇÃO PARQUET DOS CATÁLOGOS (PDF E PLANILHAS)
    # ==========================================
    exporta_catalogo_para_parquet(
        summary_pdfs, particoes_pdfs, scraper.name, config, logger, "documentos"
    )
    exporta_catalogo_para_parquet(
        summary_tables, particoes_tabelas, scraper.name, config, logger, "planilhas"
    )

    # ==========================================
    # EXPORTAÇÃO PARA O EXCEL COM 2 ABAS DE RESUMO
    # ==========================================
    os.makedirs(config.output_dir, exist_ok=True)
    excel_path = os.path.join(
        config.output_dir, f"{scraper.name.lower()}_relatorio_qualidade.xlsx"
    )

    df_summary_tables = pd.DataFrame(summary_tables)
    df_summary_pdfs = pd.DataFrame(summary_pdfs)

    if not df_summary_tables.empty:
        df_summary_tables = df_summary_tables.map(limpa_caracteres_ilegais)

    if not df_summary_pdfs.empty:
        df_summary_pdfs = df_summary_pdfs.map(limpa_caracteres_ilegais)

    structured_samples = {
        aba: df.map(limpa_caracteres_ilegais)
        for aba, df in structured_samples.items()
    }

    with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
        # Aba 1: Resumo de Planilhas e APIs Tabulares
        if not df_summary_tables.empty:
            df_summary_tables.to_excel(
                writer, sheet_name="Resumo_Geral", index=False
            )
        else:
            pd.DataFrame([{"Aviso": "Nenhuma tabela encontrada"}]).to_excel(
                writer, sheet_name="Resumo_Geral", index=False
            )

        # Aba 2: Resumo exclusivo de PDFs e Documentos
        if not df_summary_pdfs.empty:
            df_summary_pdfs.to_excel(
                writer, sheet_name="Resumo_PDFs", index=False
            )
        else:
            pd.DataFrame([{"Aviso": "Nenhum PDF encontrado"}]).to_excel(
                writer, sheet_name="Resumo_PDFs", index=False
            )

        # Demais Abas: Amostras das planilhas/APIs aprovadas
        for sheet_name, df_data in structured_samples.items():
            df_data.to_excel(writer, sheet_name=sheet_name, index=False)

    logger.info(
        f"✅ [{scraper.name}] Concluído! Tabelas: {len(summary_tables)} | "
        f"PDFs: {len(summary_pdfs)}. Relatório: {excel_path}\n"
    )
    return df_summary_tables
