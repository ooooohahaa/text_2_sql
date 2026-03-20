"""
GraphRAG 模块：从 schema 描述文档构建「表-实体-字段」关系图，
用于在 Text2SQL 时检索与自然语言问题相关的表与列。
"""

from text2sql.graphrag.schema_graph import (
    build_schema_graph,
    format_retrieved_schema_context,
    get_schema_graph_artifacts,
    get_schema_graph_index,
    get_schema_retriever,
    load_schema_index_from_pgvector,
)

__all__ = [
    "build_schema_graph",
    "format_retrieved_schema_context",
    "get_schema_graph_artifacts",
    "get_schema_graph_index",
    "get_schema_retriever",
    "load_schema_index_from_pgvector",
]
