"""
基于 LlamaIndex + GraphRAG 的 Text2SQL 工具。

- 使用 GraphRAG 构建 schema 与实体的关系图，用于检索相关表/列。
- 使用 LlamaIndex 的 NLSQLTableQueryEngine 将自然语言转为 SQL 并执行。
"""

__version__ = "0.1.0"
