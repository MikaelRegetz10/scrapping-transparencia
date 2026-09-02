# Portal

Front-end estático que consome a API RESTful de `api/`. Nenhuma dependência de
build: são HTML, CSS e JavaScript de módulo, servidos como arquivo.

## Páginas

| Arquivo | O que faz |
|---|---|
| `documentos.html` | Lista os links de PDF coletados dos portais de transparência. Cada item abre o arquivo na fonte. |
| `planilhas.html` | Lista os arquivos tabulares — CSV, Excel, JSON — com o resultado da auditoria de cada link: se respondeu, quanto pesa, se o conteúdo pôde ser lido e o que o profiler reclamou. |

As duas são catálogos de links e compartilham o mesmo esqueleto:

- `style.css` — o visual base, herdado do portal de dados abertos.
- `catalogo.css` — o chrome comum às duas: navegação, filtros, itens da lista,
  estados de vazio e de erro.
- `catalogo.js` — o que as duas fazem igual: falar com a API, contar por
  faceta, escapar o texto que veio raspado de terceiros.
- `<página>.css` / `<página>.js` — o que é próprio de cada uma.

## Rodando

Precisa dos dois processos no ar: a API lê os Parquet, o portal consome a API.

```bash
.venv/bin/uvicorn api.main_api:app --reload --port 8000
```

```bash
.venv/bin/python -m http.server 8001 --directory portal
```

As páginas ficam em <http://localhost:8001/documentos.html> e
<http://localhost:8001/planilhas.html>.

Abrir o `.html` direto do disco (`file://`) não funciona: o navegador bloqueia
módulos JavaScript nesse esquema.

### Apontando para outra API

O padrão é `http://localhost:8000`. Para usar outro endereço sem editar o
código, passe na URL:

```
planilhas.html?api=https://api.exemplo.org
```

## De onde vem o acervo

O pipeline separa cada coleta em dois catálogos, cada um no seu tema:
`tema=documentos` para os PDF e `tema=planilhas` para os arquivos tabulares.
Cada página fixa o seu tema em toda consulta. Os detalhes estão em
`core/pipeline.py`, em `TEMA_DOCUMENTOS`, `TEMA_PLANILHAS` e
`exporta_catalogo_para_parquet`.

O catálogo é o inventário dos arquivos, não o conteúdo deles: as linhas de
dentro de cada planilha aprovada no profiling vão para os temas próprios delas
(`dados_abertos`, `administracao_regional_…`) e não aparecem nestas páginas.

Se uma página abrir com "0 arquivos", o Parquet ainda não tem catálogo. Rode a
coleta — ou, para as coletas anteriores a este recorte, o backfill que lê os
Excel de qualidade já gerados:

```bash
.venv/bin/python -m scripts.backfill_planilhas
```

## O que a página de planilhas mostra

Cada item traz o que a auditoria apurou sobre aquele link:

- **Conteúdo no acervo** — o profiler leu o arquivo e as linhas foram para o
  Parquet. É consultável pela API.
- **Conteúdo não lido** — é formato tabular, mas o profiler não conseguiu
  aproveitar. O motivo aparece logo abaixo, na linha de qualidade.
- **Formato não tabular** — `.zip`, `.docx`, `.png`. O pipeline audita só a
  disponibilidade desses, sem baixar o corpo do arquivo.
- **Link fora do ar** — não respondeu na última verificação.

Os quatro números do topo são atalhos: clicar em um aplica exatamente o filtro
que o produziu.

## Limitações conhecidas

- **UF e ano são pouco úteis por enquanto.** Os scrapers não extraem essas
  informações do arquivo, então tudo cai em `DN` e no exercício corrente. Nos
  registros trazidos pelo backfill isso é certo: o Excel de qualidade não
  guarda o `tcu_uf`/`tcu_ano` do item bruto. Uma coleta nova corrige.
- **`tamanho_kb` depende do servidor de origem.** Quando o portal não manda
  `Content-Length`, o campo fica vazio e o item sai sem o peso.
- **A lista de rótulos de tipo é espelhada em dois lugares.**
  `TIPOS_DE_DOCUMENTO` existe em `core/pipeline.py` e, como
  `TIPOS_DE_DOCUMENTO`/`TIPOS_DE_DADO`, nas duas páginas. Só os rótulos
  legíveis: quais tipos existem quem diz é a API. Mudar o vocabulário do
  Python exige acrescentar o rótulo aqui, senão o nome da partição aparece cru.
