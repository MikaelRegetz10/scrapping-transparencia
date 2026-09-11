# Portal

Front-end estático que consome a API RESTful de `api/`. Nenhuma dependência de
build: são HTML, CSS e JavaScript de módulo, servidos como arquivo.

## Páginas

| Arquivo | O que faz |
|---|---|
| `documentos.html` | Lista os links de PDF coletados dos portais de transparência. Cada item abre o arquivo na fonte. |
| `planilhas.html` | Lista os arquivos tabulares — CSV, Excel, JSON — com o resultado da auditoria de cada link: se respondeu, quanto pesa, se o conteúdo pôde ser lido e o que o profiler reclamou. |
| `visualizador.html` | Abre o **conteúdo** de uma planilha coletada em grade, como numa planilha de verdade: célula selecionável, cabeçalho e numeração fixos, ordenação por coluna, busca e exportação. |

As duas primeiras são catálogos de links e compartilham o mesmo esqueleto:

- `style.css` — o visual base, herdado do portal de dados abertos.
- `catalogo.css` — o chrome comum às duas: navegação, filtros, itens da lista,
  estados de vazio e de erro.
- `catalogo.js` — o que as duas fazem igual: falar com a API, contar por
  faceta, escapar o texto que veio raspado de terceiros.
- `<página>.css` / `<página>.js` — o que é próprio de cada uma.

O visualizador usa o mesmo `style.css` e o mesmo `catalogo.js`, mas não é um
catálogo: em vez de listar links, lê linhas. O que ele tem de próprio está em
`visualizador.css` e `visualizador.js`.

## Rodando

Precisa dos dois processos no ar: a API lê os Parquet, o portal consome a API.

```bash
.venv/bin/uvicorn api.main_api:app --reload --port 8000
```

```bash
.venv/bin/python -m http.server 8001 --directory portal
```

As páginas ficam em <http://localhost:8001/documentos.html>,
<http://localhost:8001/planilhas.html> e
<http://localhost:8001/visualizador.html>.

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
(`dados_abertos`, `administracao_regional_…`) e não aparecem nas páginas de
catálogo — é o visualizador que as abre.

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
- **Baixa em Excel** — a fonte é um endpoint de API que responde JSON. Ver
  abaixo.

Os quatro números do topo são atalhos: clicar em um aplica exatamente o filtro
que o produziu.

Os itens marcados como **Conteúdo no acervo** ganham um atalho "Ver dados", que
abre aquele arquivo no visualizador.

## Os itens que baixam em Excel

Cento e trinta e dois itens do catálogo — de SESC, SENAI e SESI — não apontam
para um arquivo: apontam para um endpoint de API que devolve JSON. Clicar num
deles abria o JSON cru numa aba do navegador, que é a forma menos legível
possível de um dado que já é tabular.

Esses itens saem pelo `GET /api/v1/documentos/planilha?url=…`, que busca o link
na fonte, desempacota o JSON com o mesmo achatador do profiler e devolve um
`.xlsx`. O item leva o selo **Baixa em Excel** e a seta `↓` no lugar da `↗`,
porque um clique que baixa arquivo e outro que troca de site não devem parecer
a mesma coisa.

O endpoint só converte URL que já conste no acervo: a consulta ao catálogo é o
que o impede de virar um proxy aberto para qualquer endereço, inclusive os da
rede interna de onde a API roda. Formato que não vira planilha (`.zip`,
`.docx`, `.png`) volta 415, e o portal continua mandando esses direto à fonte —
CSV e Excel o navegador já baixa sozinho, e para eles nada mudou.

A conversão e o visualizador respondem a perguntas diferentes sobre o mesmo
item, e por isso convivem na linha: ela vai à fonte agora e entrega o arquivo
inteiro como a entidade o publica hoje; ele abre o que a última coleta guardou
no acervo, sem download e sem depender de a fonte estar no ar.

## O que o visualizador mostra

Um **conjunto** é um arquivo Parquet do acervo: o conteúdo de uma planilha
coletada, como o profiler a aprovou. São 2.591 deles, e a página abre um de
cada vez.

A grade é servida pela API, uma página por vez — nada do acervo é carregado
inteiro no navegador. O que ela oferece:

- **Endereço de célula.** A coluna tem letra e a linha tem número, e o número é
  o da linha no conjunto, não o da linha na tela: na página 3 a primeira é a
  201.
- **Barra da célula.** A coluna trunca o que não cabe; a barra mostra o valor
  inteiro do que estiver selecionado, e o botão ao lado copia. As setas do
  teclado andam pela grade.
- **Ordenação por coluna.** Clicar no cabeçalho alterna crescente, decrescente
  e sem ordenação. Quem ordena é a API, sobre o conjunto todo — não sobre a
  página exibida.
- **Filtro de linhas.** Mostra só as linhas em que o termo aparece em alguma
  coluna.
- **Colunas redimensionáveis**, arrastando a divisa no cabeçalho. A largura
  sobrevive à troca de página e à ordenação.
- **Exportação** em CSV, Excel ou JSON, com a mesma busca e a mesma ordenação
  da tela.
- **Documentação do conjunto**: tipo, preenchimento e valores distintos de cada
  coluna. É medido no arquivo, não publicado pela entidade.

As colunas `tema`, `tipo_documento`, `ano` e `uf` não aparecem na grade: o
pipeline as grava em toda linha, e dentro de um conjunto são sempre a mesma
palavra. Elas estão no cabeçalho do conjunto, uma vez só.

A página aceita dois parâmetros na URL: `?arquivo=…` abre um conjunto direto —
é o que o link compartilhável usa — e `?busca=…` deixa o seletor pré-filtrado,
abrindo sozinho quando só um conjunto casa. É por esse segundo que chega quem
clica em "Ver dados" na página de planilhas.

## Limitações conhecidas

- **O ano é pouco útil por enquanto.** Os scrapers nem sempre extraem o
  exercício do arquivo, e nos registros trazidos pelo backfill ele não tem como
  vir: o Excel de qualidade não guarda o `tcu_ano` do item bruto, então tudo cai
  no exercício corrente. Uma coleta nova corrige.
- **A UF vale nos dois catálogos, mas só o de planilhas tem estado de verdade
  hoje.** Ela sai do `tcu_uf` do scraper e, na falta dele, do texto do link
  ("Administração Regional do Acre", "SESC AC") — ver `uf_do_texto`, em
  `core/parquet_exporter.py`. No `tema=planilhas` isso dá 27 estados mais o
  `DN`; no `tema=documentos` dá `DN` em tudo, e está certo: os PDF catalogados
  são os da ABDI e do SESI nacional. O filtro do `documentos.html` só ganha
  opções quando entrar no acervo um portal regional que publique PDF.
- **`tamanho_kb` depende do servidor de origem.** Quando o portal não manda
  `Content-Length`, o campo fica vazio e o item sai sem o peso.
- **O "Ver dados" procura pelo título, não por uma chave.** O pipeline grava o
  catálogo e o conteúdo em ramos separados, sem guardar de qual conjunto veio
  cada linha do catálogo. O atalho passa o título adiante e o visualizador
  procura por ele — o que erra quando o título do link e o nome do conjunto
  divergem. Nesse caso a página não fica muda: diz que não achou e oferece
  voltar à lista inteira.
- **Cabeçalho torto na origem continua torto aqui.** Vários CSV do Sistema S
  foram gravados no Parquet com BOM e aspas no nome da primeira coluna, e
  alguns com o acento já corrompido na coleta (`CORPO TÃCNICO`). O visualizador
  limpa o BOM e as aspas para exibir; o acento quebrado é dado do acervo, e
  consertá-lo é trabalho de uma coleta nova, não da tela.
- **A lista de rótulos de tipo é espelhada em dois lugares.**
  `TIPOS_DE_DOCUMENTO` existe em `core/pipeline.py` e, como
  `TIPOS_DE_DOCUMENTO`/`TIPOS_DE_DADO`, nas duas páginas. Só os rótulos
  legíveis: quais tipos existem quem diz é a API. Mudar o vocabulário do
  Python exige acrescentar o rótulo aqui, senão o nome da partição aparece cru.
