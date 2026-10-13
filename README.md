# Scraping de Portais de Transparência

[![Online](https://img.shields.io/badge/online-fly.dev-8A2BE2?logo=fly.io&logoColor=white)](https://scrapping-transparencia.fly.dev/planilhas.html)
[![Deploy](https://github.com/MikaelRegetz10/scrapping-transparencia/actions/workflows/fly-deploy.yml/badge.svg)](https://github.com/MikaelRegetz10/scrapping-transparencia/actions/workflows/fly-deploy.yml)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)

Coleta links de dados abertos dos portais de transparência do Sistema Indústria
e entidades parceiras (ABDI, SESI, SENAI, SENAC, SENAR, SESC, SESCOOP, ApexBrasil),
verifica se cada link está no ar, audita a qualidade dos dados e publica tudo
num portal navegável, com API RESTful.

## 🌐 Projeto no ar

O portal e a API já estão publicados no [Fly.io](https://fly.io) (região GRU):

| O quê | Endereço |
|---|---|
| **Portal — planilhas** | <https://scrapping-transparencia.fly.dev/planilhas.html> |
| Portal — documentos (PDFs) | <https://scrapping-transparencia.fly.dev/documentos.html> |
| Visualizador de planilhas | <https://scrapping-transparencia.fly.dev/visualizador.html> |
| Dicionário de colunas | <https://scrapping-transparencia.fly.dev/dicionario.html> |
| Documentação interativa da API (Swagger) | <https://scrapping-transparencia.fly.dev/docs> |
| Healthcheck | <https://scrapping-transparencia.fly.dev/health> |

> A máquina é desligada quando ociosa (`auto_stop_machines`); o primeiro acesso
> depois de um tempo parado pode levar alguns segundos para responder.

Cada push na `main` dispara o deploy automático
(`.github/workflows/fly-deploy.yml`). Uma única aplicação serve a API em
`/api/v1/*` e o front-end estático na raiz.

## O que o projeto faz

1. **Coleta** — um scraper por portal (`scrapers/`), sem automação de navegador
   sempre que possível.
2. **Auditoria** — valida cada link (`core/validator.py`) e avalia se o dataset
   é estruturado (`core/profiler.py`).
3. **Acervo** — grava catálogos e datasets em Parquet (`outputs/`), além das
   planilhas Excel de diagnóstico.
4. **API** — FastAPI consulta o acervo (`api/`): filtros, documentos,
   estatísticas, conjuntos de dados, exportação.
5. **Portal** — front-end estático (`portal/`) que consome a API.

## Requisitos

- **Python 3.10 ou superior** (o código usa anotações do tipo `str | None`)
- Git

O `requirements.txt` usa faixas de versão em vez de versões travadas, então o
pip instala o que for compatível com o seu interpretador — pandas 2.x no Python
3.10, pandas 3.x no 3.12+. Não é preciso todo mundo usar a mesma versão.

## Instalação

Clone o repositório e crie o ambiente virtual dentro dele.

### Linux

O módulo `venv` vem em pacote separado em distros Debian/Ubuntu:

```bash
sudo apt install python3-venv
```

Depois:

```bash
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
```

### macOS

```bash
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
```

### Windows (PowerShell)

```powershell
py -m venv .venv ; .\.venv\Scripts\Activate.ps1 ; pip install -r requirements.txt
```

Se o PowerShell bloquear a ativação, libere para a sessão atual:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
```

## Como rodar a coleta

Com o ambiente ativado:

```bash
python main.py
```

Escolha quais portais varrer editando a lista `scrapers_to_run` em `main.py`.
O resultado vai para `outputs/<portal>_relatorio_qualidade.xlsx`, sempre na raiz
do projeto, independente do diretório de onde o script foi disparado.

## Rodando a API e o portal localmente

```bash
uvicorn api.main_api:app --reload --port 8000
```

Com isso o portal já abre em <http://localhost:8000/> (a API serve a pasta
`portal/`). Detalhes do front-end em [`portal/README.md`](portal/README.md).
Também há um `Dockerfile` usado no deploy.

## Configuração da IDE

Configurações de IDE (`.idea/`, `.vscode/`) ficam fora do controle de versão de
propósito: cada pessoa usa o editor que preferir. Basta apontar o interpretador
para o `.venv` do projeto.

- **VS Code**: `Ctrl+Shift+P` → "Python: Select Interpreter" → escolha o `.venv`
- **PyCharm**: Settings → Project → Python Interpreter → Add → Existing → `.venv`

## Estrutura

```
main.py                  ponto de entrada: escolhe os scrapers e roda o pipeline
core/pipeline.py         orquestra scraping → validação → profiling → Excel
core/validator.py        checa se cada URL responde (HEAD com fallback GET)
core/profiler.py         avalia se o dataset baixado é estruturado
core/cleaner.py          normaliza planilhas com layout "visual"
core/config.py           lê o config.json e monta o logger
scrapers/base.py         contrato que todo scraper implementa
scrapers/*.py            um módulo por portal
api/                     API RESTful que serve os Parquet gerados
portal/                  front-end estático que consome a API
scripts/                 utilitários de manutenção do acervo
outputs/                 planilhas geradas (não versionado)
logs/                    logs de execução (não versionado)
config.json              ano, delay, diretórios e nível de log
```

## Escrevendo um scraper novo

Herde de `BaseScraper`, declare as `routes` (um dicionário `{"Nome da Seção":
"https://..."}`) e implemente `extract_links()`, devolvendo uma lista de
dicionários com `source`, `section`, `title`, `context`, `download_url` e
`file_type`, mais os opcionais `file_name` e `published_at`. O resto
(validação, profiling, Excel) o pipeline faz.

`title` é o título do documento como o portal o publica. Nas páginas em que o
link é só um botão "Visualizar", o título está no texto ao lado dele: use
`separa_publicacao()` para tirar o "(Publicado em ...)" que costuma vir colado
no fim e `nome_arquivo()` para guardar o nome do PDF que será baixado.

O relatório sai com duas abas de resumo: `Resumo_Geral` para planilhas e APIs
tabulares e `Resumo_PDFs` para documentos, mais uma aba de amostra por dataset
aprovado no profiling. As duas abas viram também catálogos no Parquet
(`tema=documentos` e `tema=planilhas`), que é o que o portal exibe. O conteúdo
de cada dataset aprovado vai para o tema próprio dele, e o portal o abre em
grade no visualizador — ver `portal/README.md`.

Regra do projeto: nada de automação de navegador. Quando a página carrega
conteúdo por JavaScript (botão "Carregar Mais", abas com filtro por ano), o
caminho é achar no próprio HTML/JS a rota que a página consome e chamá-la
direto — ver `scrapers/sesi_transparencia.py` como referência.

Quando o navegador é inevitável, abra **uma sessão por rota**
(`abrir_sessoes()`, em `scrapers/navegador.py`). Portal com proteção anti-bot
marca a sessão inteira ao recusar uma requisição: na ABDI o 403 na paginação do
grid de aquisições fazia a rota seguinte, que abre normalmente sozinha, cair na
página de espera e ser registrada como bloqueada.
