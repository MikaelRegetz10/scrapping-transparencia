# Scraping de Portais de Transparência

Coleta links de dados abertos dos portais de transparência do Sistema Indústria
(ABDI, SESI, SENAI), verifica se cada link está no ar e gera uma planilha Excel
com o diagnóstico de qualidade dos dados encontrados.

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

## Como rodar

Com o ambiente ativado:

```bash
python main.py
```

Escolha quais portais varrer editando a lista `scrapers_to_run` em `main.py`.
O resultado vai para `outputs/<portal>_relatorio_qualidade.xlsx`, sempre na raiz
do projeto, independente do diretório de onde o script foi disparado.

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
(`tema=documentos` e `tema=planilhas`), que é o que o portal exibe — ver
`portal/README.md`.

Regra do projeto: nada de automação de navegador. Quando a página carrega
conteúdo por JavaScript (botão "Carregar Mais", abas com filtro por ano), o
caminho é achar no próprio HTML/JS a rota que a página consome e chamá-la
direto — ver `scrapers/sesi_transparencia.py` como referência.

Quando o navegador é inevitável, abra **uma sessão por rota**
(`abrir_sessoes()`, em `scrapers/navegador.py`). Portal com proteção anti-bot
marca a sessão inteira ao recusar uma requisição: na ABDI o 403 na paginação do
grid de aquisições fazia a rota seguinte, que abre normalmente sozinha, cair na
página de espera e ser registrada como bloqueada.
