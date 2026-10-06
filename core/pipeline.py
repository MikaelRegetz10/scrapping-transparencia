# core/pipeline.py
from collections import defaultdict
from datetime import datetime
import glob
import os
import re
import time
import duckdb
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
    uf_do_texto,
)
from core.profiler import analyze_dataset_quality
from core.validator import DEFAULT_HEADERS, check_url_status
from scrapers.base import BaseScraper

# Formatos que o core/profiler.py sabe perfilar.
PROFILABLE_TYPES = {"csv", "xlsx", "xls", "json", "ods"}

# Linhas de amostra guardadas por dataset aprovado no profiling.
ROWS_PER_SAMPLE = 20


def limpa_caracteres_ilegais(valor):
    """Remove os caracteres de controle ASCII que o formato XLSX recusa."""
    if isinstance(valor, str):
        return ILLEGAL_CHARACTERS_RE.sub("", valor)
    return valor


def sanitize_sheet_name(title: str, index: int) -> str:
    """Higieniza o título para criar abas válidas no Excel."""
    clean_title = re.sub(r"[\\/*?:\[\]]", "", title)
    short_title = clean_title[:24].strip()
    return f"{index:02d}_{short_title}" if short_title else f"Aba_{index:02d}"


def carregar_cache_urls_processadas(output_dir: str) -> dict:
    """Consulta o DuckDB e carrega todas as URLs de planilhas e APIs já catalogadas.

    Mapeia apenas a pasta 'tema=planilhas' usando union_by_name=True para tratar schemas divergentes.
    Retorna um dicionário: { 'https://url...': tamanho_kb }
    """
    if not os.path.exists(output_dir):
        return {}

    path_pattern = os.path.join(output_dir, "parquet", "tema=planilhas", "**", "*.parquet")

    if not glob.glob(path_pattern, recursive=True):
        return {}

    try:
        conn = duckdb.connect()
        # Usa read_parquet com union_by_name=True para tolerar arquivos com schemas diferentes
        query = f"""
            SELECT url_download, CAST(tamanho_kb AS DOUBLE) as tamanho_kb 
            FROM read_parquet('{path_pattern}', union_by_name=True) 
            WHERE (ativo = 'SIM' OR status_http = 200)
              AND url_download IS NOT NULL
        """
        df_cache = conn.execute(query).df()
        if df_cache.empty:
            return {}

        return dict(zip(df_cache["url_download"], df_cache["tamanho_kb"]))
    except Exception as e:
        print(f"⚠️ [Cache] Aviso ao carregar catálogo DuckDB: {e}")
        return {}


# Tema reservado aos links de documento (PDFs)
TEMA_DOCUMENTOS = "documentos"

# Tema reservado ao catálogo das planilhas e APIs
TEMA_PLANILHAS = "planilhas"

# Processo seletivo
TIPO_PROCESSO_SELETIVO = "processos_seletivos"

# Vocabulário fechado dos documentos
TIPOS_DE_DOCUMENTO = frozenset(TIPOS_CONHECIDOS | {TIPO_PROCESSO_SELETIVO})


def tipo_do_documento(item: dict, title: str, section: str) -> str:
    """Categoria de um documento, preferindo a seção de origem ao título."""
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
    """Chave Hive (tema, tipo_documento, ano, uf) de um link catalogado."""
    uf = item.get("tcu_uf") or uf_do_texto(section, title) or "DN"

    return (
        tema,
        tipo_do_documento(item, title, section),
        str(item.get("tcu_ano") or config.ano),
        sanitize_name(uf).upper(),
    )


def sim_ou_nao(valor) -> str:
    """Normaliza o `ativo`."""
    if isinstance(valor, str):
        return valor
    return "SIM" if valor else "NÃO"


def numera_status(df: pd.DataFrame) -> pd.DataFrame:
    """Deixa `status_http` inteiro, trocando por nulo o "ERRO" da verificação."""
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
    """Grava um catálogo de links no Parquet Hive."""
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
    """Executa o scraping incremental, valida os links, exporta Parquet e gera o Excel."""
    logger.info(
        f"Iniciando Auditoria Multi-Rotas Incremental: {scraper.name} (Exercício: {config.ano})"
    )

    # 1. Carrega o cache exclusivo do catálogo de planilhas/APIs
    cache_processados = carregar_cache_urls_processadas(config.output_dir)
    if cache_processados:
        logger.info(
            f"⚡ [Cache] {len(cache_processados)} recurso(s) mapeado(s) no catálogo de execuções anteriores."
        )

    raw_items = scraper.extract_links()
    total = len(raw_items)
    logger.info(
        f"[{scraper.name}] Total de links extraídos de todas as rotas: {total}"
    )

    summary_tables = []
    summary_pdfs = []
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

        # Check Conectividade leve (HEAD request)
        status_info = check_url_status(url)
        is_active = status_info["is_active"]
        status_code = status_info["status_code"] or "ERRO"
        size_kb = status_info.get("content_length_kb", 0)

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
        # 2. TRATAMENTO PARA TABELAS E APIS (CSV, XLSX, JSON)
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

            # ----------------------------------------------------
            # 🔍 VERIFICAÇÃO INCREMENTAL DE CACHE (Lógica Adaptativa)
            # ----------------------------------------------------
            ja_processado = False
            if url in cache_processados:
                tamanho_cache = cache_processados[url]
                # Para arquivos estáticos com tamanho em KB definido
                if tamanho_cache and size_kb and float(size_kb) > 0 and float(tamanho_cache) > 0:
                    ja_processado = abs(float(tamanho_cache) - float(size_kb)) < 0.1
                else:
                    # Para APIs/downloads dinâmicos que retornam Content-Length zero/chunked
                    ja_processado = True

            if ja_processado:
                # Pula o download pesado e a profiling pois já existe e não mudou
                is_structured = True
                if config.log_detalhado:
                    logger.debug(
                        f"⏭️  [{section}] Recurso/API já existente no catálogo e sem alterações. Pulando download."
                    )
            elif is_active and file_type not in PROFILABLE_TYPES:
                erros_str = (
                    f"Formato '{file_type}' fora do escopo do profiler: "
                    "link auditado apenas quanto à disponibilidade."
                )
                if config.log_detalhado:
                    logger.debug(
                        f"⏭️  Formato '{file_type}' não perfilável (download ignorado)."
                    )
            elif is_active:
                # Recurso NOVO ou ALTERADO: realiza o download e o processamento completo
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

                        # EXPORTAÇÃO PARQUET HIVE E DICIONÁRIO JSON
                        caminho_parquet = export_to_parquet(
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
                            uf=(
                                    item.get("tcu_uf")
                                    or uf_do_texto(section, title)
                                    or "DN"
                            ),
                            prefixo_nome=f"{item.get('source', 'extracao')}_{title}",
                        )

                        if caminho_parquet:
                            from core.profiler import gerar_json_dicionario_base

                            pasta_destino = os.path.dirname(caminho_parquet)
                            nome_base = os.path.splitext(
                                os.path.basename(caminho_parquet)
                            )[0]

                            gerar_json_dicionario_base(
                                df=df_valid,
                                dataset_name=nome_base,
                                output_dir=pasta_destino,
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

    # EXPORTAÇÃO PARQUET DOS CATÁLOGOS
    exporta_catalogo_para_parquet(
        summary_pdfs, particoes_pdfs, scraper.name, config, logger, "documentos"
    )
    exporta_catalogo_para_parquet(
        summary_tables, particoes_tabelas, scraper.name, config, logger, "planilhas"
    )

    # EXPORTAÇÃO PARA EXCEL
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
        if not df_summary_tables.empty:
            df_summary_tables.to_excel(
                writer, sheet_name="Resumo_Geral", index=False
            )
        else:
            pd.DataFrame([{"Aviso": "Nenhuma tabela encontrada"}]).to_excel(
                writer, sheet_name="Resumo_Geral", index=False
            )

        if not df_summary_pdfs.empty:
            df_summary_pdfs.to_excel(
                writer, sheet_name="Resumo_PDFs", index=False
            )
        else:
            pd.DataFrame([{"Aviso": "Nenhum PDF encontrado"}]).to_excel(
                writer, sheet_name="Resumo_PDFs", index=False
            )

        for sheet_name, df_data in structured_samples.items():
            df_data.to_excel(writer, sheet_name=sheet_name, index=False)

    logger.info(
        f"✅ [{scraper.name}] Concluído! Tabelas: {len(summary_tables)} | "
        f"PDFs: {len(summary_pdfs)}. Relatório: {excel_path}\n"
    )
    return df_summary_tables