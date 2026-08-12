from typing import Any, Dict, List
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