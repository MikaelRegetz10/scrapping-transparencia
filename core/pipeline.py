# core/pipeline.py
import io
import os
import re
import time
from datetime import datetime
import pandas as pd
import requests

from core.cleaner import clean_dataframe  # <--- IMPORTADO AQUI
from core.validator import DEFAULT_HEADERS, check_url_status
from scrapers.base import BaseScraper


def sanitize_sheet_name(title: str, index: int) -> str:
    """Higieniza o título para ser um nome de aba válido no Excel."""
    clean_title = re.sub(r"[\\/*?:\[\]]", "", title)
    short_title = clean_title[:24].strip()
    return f"{index:02d}_{short_title}" if short_title else f"Aba_{index:02d}"


def fetch_dataset_sample(
    url: str, file_type: str, nrows: int = 50
) -> pd.DataFrame:
    """Baixa o arquivo, aplica a limpeza de dados e retorna a amostra

    tratada.
    """
    try:
        response = requests.get(url, headers=DEFAULT_HEADERS, timeout=25)
        response.raise_for_status()

        file_stream = io.BytesIO(response.content)

        # 1. Leitura do arquivo Bruto (header=None para o cleaner processar o topo)
        if file_type in ["xlsx", "xls"] or "excel" in url.lower():
            df_raw = pd.read_excel(file_stream, header=None)
        else:
            # Tenta diferentes encodings para CSV
            df_raw = None
            for enc in ["utf-8", "latin1", "iso-8859-1"]:
                for sep in [";", ",", "\t"]:
                    try:
                        file_stream.seek(0)
                        df_raw = pd.read_csv(
                            file_stream, header=None, encoding=enc, sep=sep
                        )
                        if len(df_raw.columns) > 1:
                            break
                    except Exception:
                        continue
                if df_raw is not None and len(df_raw.columns) > 1:
                    break

            if df_raw is None:
                file_stream.seek(0)
                df_raw = pd.read_csv(
                    file_stream,
                    header=None,
                    encoding="utf-8",
                    on_bad_lines="skip",
                )

        # 2. Aplica o tratamento de dados (Lixo de cabeçalho, PT-BR float, Unpivot, etc)
        df_cleaned = clean_dataframe(df_raw)

        # 3. Retorna apenas os primeiros 'nrows' do DataFrame já tratado
        return df_cleaned.head(nrows)

    except Exception as e:
        return pd.DataFrame(
            {"Status_Leitura": [f"Erro ao ler e tratar arquivo: {str(e)}"]}
        )


def run_scraper_pipeline(
    scraper: BaseScraper,
    delay: float = 0.2,
    output_dir: str = "outputs",
    rows_per_sample: int = 20,
) -> pd.DataFrame:
    """Orquestra a verificação, download, limpeza e salvamento no Excel."""
    print(f"🚀 Iniciando scraping: {scraper.name} ({scraper.base_url})")

    raw_items = scraper.extract_links()
    total = len(raw_items)
    print(
        f"🔗 [{scraper.name}] {total} links extraídos. Verificando e tratando dados...\n"
    )

    summary_records = []
    datasets_samples = {}
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    for idx, item in enumerate(raw_items, 1):
        url = item["download_url"]
        title = item["title"]
        file_type = item["file_type"]

        print(
            f"[{idx}/{total}] Checking: {title[:30]:<30} -> ",
            end="",
            flush=True,
        )

        status_info = check_url_status(url)
        status_code = status_info["status_code"] or "ERRO"
        is_active = status_info["is_active"]

        icon = "✅" if is_active else "❌"
        print(f"{icon} (HTTP {status_code})", end="", flush=True)

        if is_active:
            print(" | 🧹 Baixando & Limpando...", end="", flush=True)
            df_sample = fetch_dataset_sample(
                url, file_type, nrows=rows_per_sample
            )

            sheet_name = sanitize_sheet_name(title, idx)
            datasets_samples[sheet_name] = df_sample
            print(" Done!", flush=True)
        else:
            print(" | ⚠️ Ignorado", flush=True)

        summary_records.append(
            {
                "id": idx,
                "fonte": item["source"],
                "titulo": title,
                "tipo_arquivo": file_type,
                "url_download": url,
                "status_code": status_code,
                "ativo": is_active,
                "tamanho_kb": status_info["content_length_kb"],
                "verificado_em": timestamp,
            }
        )

        if delay > 0:
            time.sleep(delay)

    # Exportação Final para Excel (.xlsx)
    os.makedirs(output_dir, exist_ok=True)
    excel_path = os.path.join(
        output_dir, f"{scraper.name.lower()}_dados_abertos.xlsx"
    )

    df_summary = pd.DataFrame(summary_records)

    with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
        df_summary.to_excel(writer, sheet_name="Resumo_Geral", index=False)

        for sheet_name, df_data in datasets_samples.items():
            df_data.to_excel(writer, sheet_name=sheet_name, index=False)

    print(f"\n✅ Planilha gerada com dados limpos: {excel_path}")
    return df_summary