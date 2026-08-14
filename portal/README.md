# Portal

Front-end estático que consome a API RESTful de `api/`. Nenhuma dependência de
build: são HTML, CSS e JavaScript de módulo, servidos como arquivo.

## Páginas

| Arquivo | O que faz |
|---|---|
| `documentos.html` | Lista os links de PDF coletados dos portais de transparência. Cada item abre o arquivo na fonte. |

O visual vem de `style.css`, herdado do portal de dados abertos, e cada página
acrescenta o seu (`documentos.css`).

## Rodando

Precisa dos dois processos no ar: a API lê os Parquet, o portal consome a API.

```bash
.venv/bin/uvicorn api.main_api:app --reload --port 8000
```

```bash
.venv/bin/python -m http.server 8001 --directory portal
```

A página fica em <http://localhost:8001/documentos.html>.

Abrir o `.html` direto do disco (`file://`) não funciona: o navegador bloqueia
módulos JavaScript nesse esquema.

### Apontando para outra API

O padrão é `http://localhost:8000`. Para usar outro endereço sem editar o
código, passe na URL:

```
documentos.html?api=https://api.exemplo.org
```

## De onde vêm os documentos

O pipeline grava os links de PDF numa partição própria, `tema=documentos`, e a
página fixa esse tema em toda consulta. Os detalhes estão em
`core/pipeline.py`, em `TEMA_DOCUMENTOS` e `exporta_documentos_para_parquet`.

Se a página abrir com "0 documentos", o Parquet ainda não tem nenhum: rode a
coleta com os scrapers de PDF habilitados em `main.py`.

## Limitações conhecidas

- **A busca não cobre o título.** O `search` da API casa contra tema, tipo de
  documento e UF, então procurar "edital" devolve zero mesmo havendo editais.
  A página avisa isso quando a busca não acha nada. Resolver exige incluir as
  colunas de texto na cláusula de busca de `api/database.py`.
- **UF e ano são pouco úteis por enquanto.** Os scrapers de PDF não extraem
  essas informações do documento, então tudo cai em `DN` e no exercício
  corrente. O filtro já funciona para quando a coleta passar a preenchê-las.
- **A lista de tipos é espelhada em dois lugares.** `TIPOS_DE_DOCUMENTO` existe
  em `core/pipeline.py` e em `documentos.js`; mudar um exige mudar o outro. É o
  preço de não alterar `/api/v1/filtros`, que varre todas as partições e não
  sabe filtrar por tema.
