"""
app/models/__init__.py

Imports all ORM models so Alembic's env.py can discover them via
`Base.metadata`. Always add new models here when you create them.
"""

from app.models.repository import Analysis, Repository  # noqa: F401
from app.models.research import Paper, PaperChunk  # noqa: F401
