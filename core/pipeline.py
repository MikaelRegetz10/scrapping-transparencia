# core/pipeline.py
from datetime import datetime
import io
import os
import re
import time
import pandas as pd
import requests

from core.profiler import analyze_dataset_quality
from core.validator import DEFAULT_HEADERS, check_url_status
from scrapers.base import BaseScraper


# Formatos que o core/profiler.py sabe perfilar. Os demais (pdf, zip, doc...)
# são auditados só quanto à disponibilidade, sem baixar o corpo do arquivo.
PROFILABLE_TYPES = {"csv", "xlsx", "xls", "json"}

# Raiz do projeto: as saídas vão sempre para o mesmo lugar, não importa de que
# diretório a IDE (PyCharm, VS Code, terminal) dispare o script.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_OUTPUT_DIR = os.path.join(PROJECT_ROOT, "outputs")


def sanitize_sheet_name(title: str, index: int) -> str:
    """Higieniza o título para criar abas válidas no Excel."""
    clean_title = re.sub(r"[\\/*?:\[\]]", "", title)
    short_title = clean_title[:24].strip()
    return f"{index:02d}_{short_title}" if short_title else f"Aba_{index:02d}"


def run_scraper_pipeline(
    scraper: BaseScraper,
    delay: float = 0.2,
    output_dir: str = DEFAULT_OUTPUT_DIR,
    rows_per_sample: int = 20,
) -> pd.DataFrame:
    """Executa o scraping, faz o Data Profiling e armazena apenas dados

    estruturados válidos.
    """
    print(f"🚀 Iniciando Auditoria e Scraping: {scraper.name}")
    print(f"🌐 URL Alvo: {scraper.base_url}\n" + "=" * 70)

    raw_items = scraper.extract_links()
    total = len(raw_items)
    print(f"🔗 Total de links identificados: {total}\n")

    summary_records = []
    structured_samples = {}
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    for idx, item in enumerate(raw_items, 1):
        url = item["download_url"]
        title = item["title"]
        file_type = item["file_type"]

        print(f"[{idx}/{total}] Processando: {title[:40]:<40}")
        print(f"      🔗 URL: {url}")

        # 1. Validação HTTP
        status_info = check_url_status(url)
        is_active = status_info["is_active"]
        status_code = status_info["status_code"] or "ERRO"

        is_structured = False
        erros_str = ""
        avisos_str = ""

        if is_active and file_type not in PROFILABLE_TYPES:
            erros_str = (
                f"Formato '{file_type}' fora do escopo do profiler: "
                "link auditado apenas quanto à disponibilidade."
            )
            print(f"      ⏭️  Formato '{file_type}' não perfilável (download ignorado).")
        elif is_active:
            try:
                # 2. Download do Arquivo em Memória
                res = requests.get(url, headers=DEFAULT_HEADERS, timeout=25)
                file_bytes = res.content

                # 3. Data Profiling / Análise de Qualidade
                profiling = analyze_dataset_quality(file_bytes, file_type)
                is_structured = profiling["is_structured"]
                erros = profiling["errors"]
                avisos = profiling["warnings"]

                erros_str = " | ".join(erros) if erros else "Nenhum"
                avisos_str = " | ".join(avisos) if avisos else "Nenhum"

                if is_structured:
                    print("      ✅ QUALIDADE EXCELENTE: Dado Estruturado.")
                    sheet_name = sanitize_sheet_name(title, idx)
                    structured_samples[sheet_name] = profiling[
                        "df_valid"
                    ].head(rows_per_sample)
                    print(
                        f"      📥 Amostra adicionada à aba: '{sheet_name}'"
                    )
                else:
                    print("      ❌ DADO DESESTRUTURADO (Rejeitado para aba):")
                    for err in erros:
                        print(f"         - {err}")

                if avisos:
                    for avs in avisos:
                        print(f"         ⚠️ {avs}")

            except Exception as e:
                erros_str = f"Falha no download/profiling: {e}"
                print(f"      ❌ Erro de processamento: {e}")
        else:
            erros_str = f"Link inativo (HTTP {status_code})"
            print(f"      ❌ Link Inativo (HTTP {status_code})")

        print("-" * 70)

        # Registro Diagnóstico Completo para a aba 'Resumo_Geral'
        summary_records.append(
            {
                "id": idx,
                "fonte": item["source"],
                "titulo": title,
                "contexto": item.get("context", ""),
                "tipo_arquivo": file_type,
                "url_download": url,
                "status_http": status_code,
                "ativo": is_active,
                "content_type": status_info.get("content_type") or "",
                "tamanho_kb": status_info.get("content_length_kb"),
                "estruturado": "SIM" if is_structured else "NÃO",
                "erros_qualidade": erros_str,
                "avisos_qualidade": avisos_str,
                "verificado_em": timestamp,
            }
        )

        if delay > 0:
            time.sleep(delay)

    # 4. Geração do Arquivo Excel Final
    os.makedirs(output_dir, exist_ok=True)
    excel_path = os.path.join(
        output_dir, f"{scraper.name.lower()}_relatorio_qualidade.xlsx"
    )

    df_summary = pd.DataFrame(summary_records)

    with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
        # Aba 1: Resumo com Diagnóstico e Status de Estruturação
        df_summary.to_excel(writer, sheet_name="Resumo_Geral", index=False)

        # Demais Abas: Apenas planilhas APROVADAS no Data Profiling
        for sheet_name, df_data in structured_samples.items():
            df_data.to_excel(writer, sheet_name=sheet_name, index=False)

    print("\n" + "=" * 70)
    print("RESUMO FINAL DA EXECUÇÃO:")
    print(f"📊 Total de Arquivos Analisados: {total}")
    print(
        f"✅ Planilhas Estruturadas Aprovadas (Salvas): {len(structured_samples)}"
    )
    print(
        f"❌ Planilhas Desestruturadas (Rejeitadas): {total - len(structured_samples)}"
    )
    print(f"📁 Relatório e Amostras Salvos em: {excel_path}")
    print("=" * 70)

    return df_summary