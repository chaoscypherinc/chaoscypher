---
id: adding-features
title: Adding Features
description: Step-by-step guide to adding a new Vertical Slice feature to the Chaos Cypher Cortex backend — models, repository, service, and API in one self-contained directory.
---

# Adding Features

Step-by-step guide to adding a new feature to the Cortex backend using Vertical Slice Architecture.

## Overview

Each feature is a self-contained directory with its own models, repository, service, and API. Follow these steps to add a new feature.

## Step 1: Create the Feature Directory

```
packages/cortex/src/chaoscypher_cortex/features/{feature}/
├── __init__.py
├── models.py
├── repository.py
├── service.py
└── api.py
```

## Step 2: Define the Database Model

Add your SQLModel entity to the shared database models:

```python
# packages/cortex/src/chaoscypher_cortex/shared/database/models.py

class MyEntity(SQLModel, table=True):
    """My new entity."""

    __tablename__ = "my_entities"

    id: str = Field(primary_key=True)
    name: str
    description: str | None = None
    database_name: str = Field(index=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
```

## Step 3: Create DTOs

Define Pydantic request/response models in `models.py`:

```python
# {feature}/models.py

from pydantic import BaseModel

class CreateMyEntityRequest(BaseModel):
    """Request to create a new entity."""

    name: str
    description: str | None = None

class MyEntityResponse(BaseModel):
    """Response for a single entity."""

    id: str
    name: str
    description: str | None
    created_at: str
    updated_at: str
```

## Step 4: Create the Repository

Data access layer using SQLModel:

```python
# {feature}/repository.py

from typing import TYPE_CHECKING
from sqlalchemy.orm import load_only
from sqlmodel import func, select

if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite import SqliteAdapter

class MyRepository:
    """Data access for my entities."""

    def __init__(self, adapter: "SqliteAdapter", database_name: str) -> None:
        self.adapter = adapter
        self.database_name = database_name

    def list_entities(self, *, page: int = 1, page_size: int = 50) -> tuple[list[MyEntity], int]:
        """List entities with pagination.

        Returns:
            Tuple of (entities, total_count).
        """
        session = self.adapter.session
        assert session is not None
        offset = (page - 1) * page_size
        statement = (
            select(MyEntity)
            .options(
                load_only(
                    MyEntity.id,
                    MyEntity.name,
                    MyEntity.database_name,
                    MyEntity.created_at,
                    MyEntity.updated_at,
                )
            )
            .where(MyEntity.database_name == self.database_name)
            .offset(offset)
            .limit(page_size)
        )
        entities = list(session.exec(statement))
        total = session.exec(
            select(func.count()).select_from(MyEntity).where(
                MyEntity.database_name == self.database_name
            )
        ).one()
        return entities, total

    def get_entity(self, entity_id: str) -> MyEntity | None:
        """Get entity by ID."""
        session = self.adapter.session
        assert session is not None
        statement = select(MyEntity).where(
            MyEntity.id == entity_id,
            MyEntity.database_name == self.database_name,
        )
        return session.exec(statement).first()

    def create_entity(self, entity: MyEntity) -> MyEntity:
        """Create a new entity."""
        session = self.adapter.session
        assert session is not None
        with self.adapter.transaction():
            session.add(entity)
        session.refresh(entity)
        return entity
```

:::warning[Performance]

For list operations, use `load_only()` to avoid loading large columns. See the SQLAlchemy Query Performance section in `CLAUDE.md` for details.

:::

## Step 5: Create the Service

Business logic layer — receives and returns dicts:

```python
# {feature}/service.py

import math
from chaoscypher_core import generate_id

class MyService:
    """Business logic for my entities."""

    def __init__(self, repository: MyRepository) -> None:
        self.repository = repository

    def list_entities(self, *, page: int = 1, page_size: int = 50) -> dict:
        """List entities with a paginated envelope.

        Returns:
            Dict with ``data`` (list of entity dicts) and ``pagination`` metadata.
        """
        entities, total = self.repository.list_entities(page=page, page_size=page_size)
        total_pages = max(1, math.ceil(total / page_size))
        return {
            "data": [self._to_dict(e) for e in entities],
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": total_pages,
            },
        }

    def create_entity(self, data: dict) -> dict:
        """Create a new entity."""
        entity = MyEntity(
            id=generate_id(),
            name=data["name"],
            description=data.get("description"),
            database_name=self.repository.database_name,
        )
        created = self.repository.create_entity(entity)
        return self._to_dict(created)

    def _to_dict(self, entity: MyEntity) -> dict:
        """Convert entity to dict."""
        return {
            "id": entity.id,
            "name": entity.name,
            "description": entity.description,
            "created_at": entity.created_at.isoformat(),
            "updated_at": entity.updated_at.isoformat(),
        }
```

## Step 6: Create the API with Factory

```python
# {feature}/api.py

from typing import Annotated
from fastapi import APIRouter, Depends, Query
from chaoscypher_core.app_config import Settings, get_settings
from chaoscypher_core.database import get_sqlite_adapter

router = APIRouter(prefix="/myentities", tags=["My Entities"])


def get_my_service(
    settings: Annotated[Settings, Depends(get_settings)],
) -> MyService:
    """Factory function for MyService."""
    adapter = get_sqlite_adapter(database_name=settings.current_database)
    repository = MyRepository(adapter, settings.current_database)
    return MyService(repository)


@router.get("/")
async def list_entities(
    service: Annotated[MyService, Depends(get_my_service)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=1000),
) -> dict:
    """List all entities with pagination."""
    return service.list_entities(page=page, page_size=page_size)


@router.post("/", status_code=201)
async def create_entity(
    data: CreateMyEntityRequest,
    service: Annotated[MyService, Depends(get_my_service)],
) -> dict:
    """Create a new entity."""
    return service.create_entity(data.model_dump())
```

## Step 7: Create Barrel Exports

```python
# {feature}/__init__.py
"""My feature — description."""

from .service import MyService

__all__ = ["MyService"]
```

## Step 8: Register the Router

Add the router to the API:

```python
# packages/cortex/src/chaoscypher_cortex/api/v1/router.py

# At the top of the module, alongside the other router imports:
from chaoscypher_cortex.features.my_feature.api import router as my_feature_router

# Inside create_api_router(), mount it on the local `api` router with an
# explicit prefix and tags (matching the existing registrations):
api.include_router(my_feature_router, prefix="/myentities", tags=["My Entities"])
```

Mind the prefix-ordering convention in `create_api_router()`: routers whose paths
could otherwise be captured by another router's `/{id}` pattern (for example
`/sources/tags` before `/sources`) must be mounted first.

## Checklist

- [ ] SQLModel entity defined with `database_name` field
- [ ] Pydantic DTOs for request/response
- [ ] Repository accepts `SqliteAdapter`, uses `load_only()` for list operations, and `adapter.transaction()` for writes (CC011)
- [ ] Service `list_entities` returns canonical paginated envelope `{data, pagination}`
- [ ] API route handlers are `async def` (CC033)
- [ ] Factory function named `get_{feature}_service()` (CC001)
- [ ] `__init__.py` with barrel exports and `__all__`
- [ ] Router registered in `router.py`
- [ ] Google-style docstrings on all public classes/functions
- [ ] Tests for service and API layer
