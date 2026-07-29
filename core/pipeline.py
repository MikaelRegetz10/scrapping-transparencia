from datetime import datetime
import os
import re
import time
import pandas as pd
import requests

from core.config import Config
from core.profiler import analyze_dataset_quality
from core.validator import DEFAULT_HEADERS, check_url_status
from scrapers.base import BaseScraper


def sanitize_sheet_name(title: str, index: int) -> str:
    clean_title = re.sub(r"[\\/*?:\[\]]", "", title)
    short_title = clean_title[:24].strip()
    return f"{index:02d}_{short_title}" if short_title else f"Aba_{index:02d}"


def run_scraper_pipeline(
    scraper: BaseScraper, config: Config, logger
) -> pd.DataFrame:
    logger.info(f"🚀 Iniciando Auditoria: {scraper.name} (Exercício: {config.ano})")

    raw_items = scraper.extract_links()
    total = len(raw_items)
    logger.info(f"🔗 [{scraper.name}] Total de links/endpoints: {total}")

    summary_records = []
    structured_samples = {}
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    for idx, item in enumerate(raw_items, 1):
        url = item["download_url"]
        title = item["title"]
        file_type = item["file_type"]

        if config.log_detalhado:
            logger.debug(f"[{idx}/{total}] Processando: {title}")

        status_info = check_url_status(url)
        is_active = status_info["is_active"]
        status_code = status_info["status_code"] or "ERRO"

        is_structured = False
        erros_str = ""
        avisos_str = ""

        if is_active:
            try:
                res = requests.get(url, headers=DEFAULT_HEADERS, timeout=25)
                profiling = analyze_dataset_quality(res.content, file_type)
                is_structured = profiling["is_structured"]

                erros_str = " | ".join(profiling["errors"]) if profiling["errors"] else "Nenhum"
                avisos_str = " | ".join(profiling["warnings"]) if profiling["warnings"] else "Nenhum"

                if is_structured:
                    sheet_name = sanitize_sheet_name(title, idx)
                    structured_samples[sheet_name] = profiling["df_valid"].head(20)
                    if config.log_detalhado:
                        logger.debug(f"   ✅ APROVADO: Adicionado na aba '{sheet_name}'")
                else:
                    if config.log_detalhado:
                        logger.debug(f"   ❌ REJEITADO: {erros_str}")

            except Exception as e:
                erros_str = f"Falha de processamento: {e}"
                logger.error(f"   ❌ Erro ao baixar {url}: {e}")
        else:
            erros_str = f"Link inativo (HTTP {status_code})"
            if config.log_detalhado:
                logger.debug(f"   ❌ INATIVO (HTTP {status_code})")

        summary_records.append({
            "id": idx,
            "fonte": item["source"],
            "titulo": title,
            "tipo_arquivo": file_type,
            "url_download": url,
            "status_http": status_code,
            "ativo": is_active,
            "estruturado": "SIM" if is_structured else "NÃO",
            "erros_qualidade": erros_str,
            "avisos_qualidade": avisos_str,
            "verificado_em": timestamp,
        })

        if config.delay_entre_requisicoes > 0:
            time.sleep(config.delay_entre_requisicoes)

    # Gravação do arquivo Excel final
    os.makedirs(config.output_dir, exist_ok=True)
    excel_path = os.path.join(config.output_dir, f"{scraper.name.lower()}_relatorio_qualidade.xlsx")

    df_summary = pd.DataFrame(summary_records)
    with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
        df_summary.to_excel(writer, sheet_name="Resumo_Geral", index=False)
        for sheet_name, df_data in structured_samples.items():
            df_data.to_excel(writer, sheet_name=sheet_name, index=False)

    logger.info(
        f"✅ [{scraper.name}] Finalizado! Aprovadas: {len(structured_samples)}/{total}. Relatório: {excel_path}\n"
    )
    return df_summary