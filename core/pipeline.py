# core/pipeline.py
from datetime import datetime
import os
import re
import time
import pandas as pd
import requests

from core.config import Config
from core.parquet_exporter import export_to_parquet  # <--- ADICIONADO
from core.profiler import analyze_dataset_quality
from core.validator import DEFAULT_HEADERS, check_url_status
from scrapers.base import BaseScraper


# Formatos que o core/profiler.py sabe perfilar. Os demais (zip, doc, docx...)
# são auditados só quanto à disponibilidade, sem baixar o corpo do arquivo.
PROFILABLE_TYPES = {"csv", "xlsx", "xls", "json"}

# Linhas de amostra guardadas por dataset aprovado no profiling.
ROWS_PER_SAMPLE = 20


def sanitize_sheet_name(title: str, index: int) -> str:
    """Higieniza o título para criar abas válidas no Excel."""
    clean_title = re.sub(r"[\\/*?:\[\]]", "", title)
    short_title = clean_title[:24].strip()
    return f"{index:02d}_{short_title}" if short_title else f"Aba_{index:02d}"


def run_scraper_pipeline(
    scraper: BaseScraper, config: Config, logger
) -> pd.DataFrame:
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
                "contexto": item.get("context", ""),
                "tipo_arquivo": "PDF",
                "url_download": url,
                "status_http": status_code,
                "ativo": "SIM" if is_active else "NÃO",
                "tamanho_kb": size_kb,
                "verificado_em": timestamp,
            })
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
                        structured_samples[sheet_name] = df_valid.head(20)

                        # ==========================================
                        # EXPORTAÇÃO PARQUET HIVE (TEMA / TIPO_DOC / ANO / UF)
                        # ==========================================
                        tema = item.get("tcu_tema") or section
                        tipo_documento = (
                                item.get("tcu_tipo_documento")
                                or item.get("tipo_documento")
                                or title
                        )
                        ano = item.get("tcu_ano") or config.ano
                        uf = item.get("tcu_uf") or "DN"
                        prefixo = f"{item.get('source', 'extracao')}_{title}"

                        export_to_parquet(
                            df=df_valid,
                            entidade=scraper.name,
                            base_dir=config.output_dir,
                            tema=tema,
                            tipo_documento=tipo_documento,
                            ano=ano,
                            uf=uf,
                            prefixo_nome=prefixo
                        )

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
                "contexto": item.get("context", ""),
                "tipo_arquivo": file_type,
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
            table_idx += 1

        if config.delay_entre_requisicoes > 0:
            time.sleep(config.delay_entre_requisicoes)

    # ==========================================
    # EXPORTAÇÃO PARA O EXCEL COM 2 ABAS DE RESUMO
    # ==========================================
    os.makedirs(config.output_dir, exist_ok=True)
    excel_path = os.path.join(
        config.output_dir, f"{scraper.name.lower()}_relatorio_qualidade.xlsx"
    )

    df_summary_tables = pd.DataFrame(summary_tables)
    df_summary_pdfs = pd.DataFrame(summary_pdfs)

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
        f"[{scraper.name}] Concluído! Tabelas: {len(summary_tables)} | PDFs: {len(summary_pdfs)}. Relatório: {excel_path}\n"
    )
    return df_summary_tables
