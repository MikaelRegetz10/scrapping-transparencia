from typing import Any, Dict, List, Optional
from pydantic import BaseModel


class PaginatedResponse(BaseModel):
    total: int
    page: int
    page_size: int
    total_pages: int
    data: List[Dict[str, Any]]


class FilterOptionsResponse(BaseModel):
    temas: List[str]
    tipos_documento: List[str]
    anos: List[int]
    ufs: List[str]
    entidades: List[str]
    tipos_arquivo: List[str]


class Contagem(BaseModel):
    valor: str
    total: int


class CountsResponse(BaseModel):
    por: str
    total: int
    contagens: List[Contagem]


class Conjunto(BaseModel):
    """Um dataset tabular do acervo — na prática, um arquivo Parquet."""

    arquivo: str
    nome: str
    tema: str
    entidade: str
    tipo_documento: str
    ano: str
    uf: str
    linhas: Optional[int] = None
    colunas: Optional[int] = None


class ConjuntosResponse(BaseModel):
    total: int
    page: int
    page_size: int
    total_pages: int
    data: List[Conjunto]


class Coluna(BaseModel):
    # `nome` é como a coluna se chama no arquivo — é o que o filtro de
    # ordenação aceita. `rotulo` é o mesmo nome sem o BOM e as aspas que
    # vieram do CSV de origem, e é o que se mostra.
    nome: str
    rotulo: str
    tipo: str
    # Só o dicionário de dados mede estes três; a grade devolve nome e tipo.
    preenchidas: Optional[int] = None
    distintos: Optional[int] = None
    preenchimento: Optional[float] = None


class GradeResponse(BaseModel):
    """Uma fatia de um conjunto, como a grade a desenha.

    As linhas são listas na ordem de `colunas`, e não dicionários: numa
    planilha larga o nome da coluna repetido em cada célula seria a maior
    parte da resposta.
    """

    arquivo: str
    nome: str
    tema: str
    entidade: str
    tipo_documento: str
    ano: str
    uf: str
    colunas: List[Coluna]
    linhas: List[List[Any]]
    total: int
    total_filtrado: int
    page: int
    page_size: int
    total_pages: int


class DicionarioResponse(BaseModel):
    arquivo: str
    total: int
    colunas: List[Coluna]
