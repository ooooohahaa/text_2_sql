"""
配置：从环境变量或 .env 加载数据库连接、LLM/Embedding 模型配置等。
"""
import os
from pathlib import Path

from dotenv import load_dotenv

# 优先从项目根目录加载 .env
_env_path = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(_env_path)


def get_database_url() -> str:
    """获取数据库连接 URL（MySQL）。"""
    url = os.getenv(
        "DATABASE_URL",
        "mysql+pymysql://user:password@localhost:3306/DB_SPLAN_00?charset=utf8mb4",
    )
    return url


def get_openai_api_key() -> str:
    """兼容旧版：获取统一 API Key。"""
    key = os.getenv("OPENAI_API_KEY", "")
    if not key:
        raise ValueError("请设置环境变量 OPENAI_API_KEY 或在 .env 中配置")
    return key


def _get_required_env(name: str, default: str = "") -> str:
    """读取必填环境变量，若缺失则抛错。"""
    value = os.getenv(name, default).strip()
    if not value:
        raise ValueError(f"请设置环境变量 {name} 或在 .env 中配置")
    return value


def get_llm_provider_config() -> dict:
    """
    获取 LLM 配置（与 Embedding 分离）。

    优先读取：
    - LLM_BASE_URL
    - LLM_API_KEY
    - LLM_MODEL

    向后兼容：
    - 若 LLM_API_KEY 未配置，回退 OPENAI_API_KEY
    """
    base_url = _get_required_env("LLM_BASE_URL")
    model = _get_required_env("LLM_MODEL")
    api_key = os.getenv("LLM_API_KEY", "").strip() or os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise ValueError("请设置 LLM_API_KEY（或兼容使用 OPENAI_API_KEY）")
    return {
        "base_url": base_url,
        "api_key": api_key,
        "model": model,
    }


def get_embedding_provider_config() -> dict:
    """
    获取 Embedding 配置（与 LLM 独立）。

    优先读取：
    - EMBEDDING_BASE_URL
    - EMBEDDING_API_KEY
    - EMBEDDING_MODEL

    兼容策略：
    - EMBEDDING_BASE_URL 缺失时，回退 LLM_BASE_URL
    - EMBEDDING_API_KEY 缺失时，回退 LLM_API_KEY，再回退 OPENAI_API_KEY
    """
    base_url = os.getenv("EMBEDDING_BASE_URL", "").strip() or os.getenv("LLM_BASE_URL", "").strip()
    if not base_url:
        raise ValueError("请设置 EMBEDDING_BASE_URL（或至少设置 LLM_BASE_URL 作为回退）")

    model = _get_required_env("EMBEDDING_MODEL")
    api_key = (
        os.getenv("EMBEDDING_API_KEY", "").strip()
        or os.getenv("LLM_API_KEY", "").strip()
        or os.getenv("OPENAI_API_KEY", "").strip()
    )
    if not api_key:
        raise ValueError("请设置 EMBEDDING_API_KEY（或 LLM_API_KEY / OPENAI_API_KEY 作为回退）")

    return {
        "base_url": base_url,
        "api_key": api_key,
        "model": model,
    }


def get_schema_description_path() -> Path:
    """获取 schema 描述文档路径（用于 GraphRAG）。"""
    default = Path(__file__).resolve().parents[1] / "schema_description_for_graphrag.md"
    return Path(os.getenv("SCHEMA_DESCRIPTION_PATH", str(default)))


def get_pgvector_config() -> dict:
    """
    获取 pgvector 向量库配置（用于 GraphRAG 向量索引持久化）。

    约定：
    - 若未配置 PGVECTOR_URL，则默认不启用 pgvector（回退内存向量库）
    - PGVECTOR_EMBED_DIM 默认 3072（text-embedding-3-large）
    """
    url = os.getenv("PGVECTOR_URL", "").strip()
    if not url:
        return {"enabled": False}

    table_name = os.getenv("PGVECTOR_TABLE", "schema_embeddings").strip() or "schema_embeddings"
    schema_name = os.getenv("PGVECTOR_SCHEMA", "public").strip() or "public"
    embed_dim = int(os.getenv("PGVECTOR_EMBED_DIM", "3072").strip() or "3072")

    return {
        "enabled": True,
        "url": url,
        "table_name": table_name,
        "schema_name": schema_name,
        "embed_dim": embed_dim,
    }
