"""Pydantic schemas for API request/response validation."""
from __future__ import annotations
from typing import Any, Literal, Optional
from pydantic import BaseModel, Field


class CopilotRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    store_id: Optional[str] = None
    product_id: Optional[str] = None
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    max_items: int = Field(default=8, ge=1, le=50)
    use_llm: bool = True


class AttentionRequest(BaseModel):
    scope: Literal["all", "stockout", "overstock", "slow_moving", "spike", "drop"] = "all"
    store_id: Optional[str] = None
    max_items: int = Field(default=20, ge=1, le=100)


class ProductQuery(BaseModel):
    q: str = Field(default="", max_length=200)


class UserLogin(BaseModel):
    email: str
    password: str


class ProductQueryParams(BaseModel):
    q: str = Field(default="", max_length=100)
    category: Optional[str] = None
    brand: Optional[str] = None
    sort: str = Field(default="revenue", pattern="^(revenue|units|stock|margin|name|risk)$")
    order: str = Field(default="desc", pattern="^(asc|desc)$")
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=50)


class Dashboard(BaseModel):
    health: dict[str, Any]
    options: dict[str, Any]

    @classmethod
    def resource(cls) -> str:
        return "dashboard"


ALLOWED_FILTERS = {"store_id", "category", "product_id", "date_from", "date_to"}