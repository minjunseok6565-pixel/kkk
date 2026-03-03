"""News generation pipeline.

Internal package used by the server-facing facade `news_ai.py`.
"""

from .models import NewsArticle, NewsEvent

__all__ = ["NewsEvent", "NewsArticle"]
