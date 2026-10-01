# Portal

Front-end estático que consome a API RESTful de `api/`. Nenhuma dependência de
build: são HTML, CSS e JavaScript de módulo, servidos como arquivo.

## Páginas

| Arquivo | O que faz |
|---|---|
| `index.html` | A porta de entrada: explica o caminho que o dado percorre — coleta, auditoria, acervo, API, portal — e mede o acervo pela própria API. É a única página que continua útil com a API fora do ar: o texto é fixo, só os números ficam em "—". |
| `documentos.html` | Lista os links de PDF coletados dos portais de transparência. Cada item abre o arquivo na fonte. |
| `planilhas.html` | Lista os arquivos tabulares — CSV, Excel, JSON — com o resultado da auditoria de cada link: se respondeu, quanto pesa, se o conteúdo pôde ser lido e o que o profiler reclamou. |
| `visualizador.html` | Abre o **conteúdo** de uma planilha coletada em grade, como numa planilha de verdade: célula selecionável, cabeçalho e numeração fixos, ordenação por coluna, busca e exportação. |
| `dicionario.html` | Descreve as **colunas** de um conjunto: tipo, preenchimento, valores distintos e o que cada medida dessas revela — coluna vazia, coluna de um valor só, coluna que é identificador. |

A inicial tem o `index.css` e o `index.js` só dela, e do tronco comum usa o
`style.css` e, do `catalogo.js`, o endereço da API e o formatador de número.

As duas seguintes são catálogos de links e compartilham o mesmo esqueleto:

- `style.css` — o visual base, herdado do portal de dados abertos.
- `catalogo.css` — o chrome comum às duas: navegação, filtros, itens da lista,
  estados de vazio e de erro.
- `catalogo.js` — o que as duas fazem igual: falar com a API, contar por
  faceta, escapar o texto que veio raspado de terceiros.
- `<página>.css` / `<página>.js` — o que é próprio de cada uma.

O visualizador e o dicionário usam o mesmo `style.css` e o mesmo `catalogo.js`,
mas não são catálogos: em vez de listar links, um lê linhas e o outro descreve
colunas. O que cada um tem de próprio está no seu `.css` e no seu `.js`.

## Rodando

Precisa dos dois processos no ar: a API lê os Parquet, o portal consome a API.

```bash
.venv/bin/uvicorn api.main_api:app --reload --port 8000
```

```bash
.venv/bin/python -m http.server 8001 --directory portal
```

A inicial fica em <http://localhost:8001/> e leva às outras quatro:
<http://localhost:8001/documentos.html>,
<http://localhost:8001/planilhas.html>,
<http://localhost:8001/visualizador.html> e
<http://localhost:8001/dicionario.html>.

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

## O que o dicionário de dados mostra

As entidades do Sistema S publicam a planilha e não publicam o dicionário: vem
o arquivo, não vem o que cada campo significa. A página não inventa esse
significado — mostra o que dá para **medir** no próprio conjunto, e diz isso
antes de mostrar qualquer número.

Por coluna: a letra (A, B, … AA, como numa planilha), o rótulo, o tipo, a
fração preenchida, quantas linhas têm valor e quantos valores distintos há.

O que a página acrescenta à medida crua é a leitura dela. "193 de 193, 1 valor
distinto" está correto e não diz nada; a página traduz em cinco diagnósticos:

| Diagnóstico | Quando | O que significa |
|---|---|---|
| **Sem nenhum valor** | nada preenchido | O campo foi publicado em branco na fonte. |
| **Um valor só** | 1 valor distinto | Não distingue uma linha da outra — é rótulo do conjunto, não dado. |
| **Preenchida em parte** | menos de 50% | Conta feita sobre ela cobre uma fração do conjunto. |
| **Um valor por linha** | nenhum valor se repete | Comportamento de identificador — nº de processo, de contrato. |
| **N categorias** | poucos valores, muito repetidos | Funciona como classificação, e dá um filtro útil. |

Os dois primeiros e o terceiro contam como **ressalva** e aparecem no número do
resumo; os dois últimos são informação, não defeito, e por isso não entram na
conta nem ganham cor de alerta.

A faixa colorida do resumo é a distribuição dos tipos. Num acervo raspado de
portal ela é o indicador mais direto de quanto a fonte tipou os próprios dados,
e na maioria dos conjuntos a resposta é "nada": a faixa vem inteira em VARCHAR.

A tabela filtra por nome de coluna e por diagnóstico, no navegador — o conjunto
tem dezenas de colunas, não milhares, e não vale uma ida à API por tecla.

### Uma medida só, dois consumidores

O mesmo número é entregue de duas formas, e é de propósito que saia de uma
função só — `medir_colunas`, em `core/dictionary_generator.py`:

- a exportação grava um `<arquivo>_dictionary.json` ao lado de cada Parquet,
  para quem consome o acervo direto, sem passar pelo portal;
- a API responde `GET /api/v1/conjuntos/colunas`, que é o que esta página e o
  card do visualizador leem.

Duas implementações da mesma estatística divergem com o tempo, e um acervo que
se descreve de dois jeitos diferentes não se descreve. A ressalva viaja junto
da medida nos dois caminhos, e não só no HTML: o JSON que acompanha o Parquet
vai longe da página que explicaria de onde vieram aqueles números.

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
- **O dicionário descreve o arquivo, não o campo.** Ele diz que uma coluna se
  chama `NUM_INST`, que é texto e que nunca se repete; não diz o que a entidade
  entende por instrumento. Essa parte não existe em lugar nenhum do acervo
  porque não existe na fonte, e a página prefere dizer isso a preencher o vazio
  com adivinhação.
- **Os diagnósticos têm cortes arbitrários.** "Preenchida em parte" é abaixo de
  50%, e "categoria" é até 25 valores distintos — `LIMITE_PARCIAL` e
  `MAXIMO_CATEGORIAS`, em `portal/dicionario.js`. São escolhas de leitura, não
  achados sobre o dado.
- **Os tipos de hoje são quase todos `VARCHAR`.** O `core/cleaner.py` ganhou
  tipagem de data, moeda e CNPJ, mas os Parquet em disco são anteriores a ela.
  Uma coleta nova muda a faixa de tipos do resumo.
- **A lista de rótulos de tipo é espelhada em dois lugares.**
  `TIPOS_DE_DOCUMENTO` existe em `core/pipeline.py` e, como
  `TIPOS_DE_DOCUMENTO`/`TIPOS_DE_DADO`, nas duas páginas. Só os rótulos
  legíveis: quais tipos existem quem diz é a API. Mudar o vocabulário do
  Python exige acrescentar o rótulo aqui, senão o nome da partição aparece cru.
