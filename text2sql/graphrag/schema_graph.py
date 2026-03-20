"""
从 schema 描述 Markdown 构建 GraphRAG。

实现思路参考 LlamaIndex GraphRAG 官方流程：
1) 提取图结构（实体、关系）
2) 对图做社区划分
3) 生成社区摘要
4) 将节点与社区摘要向量化并写入 pgvector（可选）
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from llama_index.core import Document, StorageContext, VectorStoreIndex
from sqlalchemy.engine import make_url


@dataclass(frozen=True)
class ColumnInfo:
    name: str
    col_type: str
    meaning: str


@dataclass(frozen=True)
class TableInfo:
    name: str
    entity: str
    columns: list[ColumnInfo]


@dataclass(frozen=True)
class GraphEdge:
    src: str
    relation: str
    dst: str


_USER_KEY_ALIASES = {
    "userid",
    "userId",
    "uid",
}
_SERVER_KEY_ALIASES = {
    "svrid",
    "svrId",
    "serverId",
}


def _normalize_key(name: str) -> str:
    if name in _USER_KEY_ALIASES:
        return "user_key"
    if name in _SERVER_KEY_ALIASES:
        return "server_key"
    return name.lower()


def _parse_schema_markdown(content: str) -> list[TableInfo]:
    """解析 schema 描述 Markdown。"""
    tables: list[TableInfo] = []
    block_pattern = re.compile(
        r"###\s*\d+\.\s*(?P<table>\w+)[^\n]*\n+(?P<block>.*?)(?=###\s*\d+\.|\Z)",
        re.DOTALL,
    )
    entity_pattern = re.compile(r"\*\*实体含义\*\*[：:]\s*(.+?)(?=\n\n|\n\||$)", re.DOTALL)
    table_row = re.compile(r"\|\s*(?P<col>\w+)\s*\|\s*([^|]+)\s*\|\s*([^|]+)\s*\|")

    for m in block_pattern.finditer(content):
        table_name = m.group("table").strip()
        block = m.group("block").strip()
        entity_m = entity_pattern.search(block)
        entity = entity_m.group(1).strip() if entity_m else ""

        cols: list[ColumnInfo] = []
        for row in table_row.finditer(block):
            col_name = row.group("col").strip()
            if col_name in ("字段名", "---"):
                continue
            cols.append(
                ColumnInfo(
                    name=col_name,
                    col_type=row.group(2).strip(),
                    meaning=row.group(3).strip(),
                )
            )

        tables.append(TableInfo(name=table_name, entity=entity, columns=cols))
    return tables


def _build_schema_graph(tables: list[TableInfo]) -> tuple[set[str], list[GraphEdge]]:
    """构建图谱三元组：表、字段、主题社区候选。"""
    nodes: set[str] = set()
    edges: list[GraphEdge] = []
    col_buckets: dict[str, list[str]] = defaultdict(list)

    for table in tables:
        table_node = f"table::{table.name}"
        nodes.add(table_node)
        if table.entity:
            entity_node = f"entity::{table.entity}"
            nodes.add(entity_node)
            edges.append(GraphEdge(src=table_node, relation="描述为", dst=entity_node))
        for col in table.columns:
            col_node = f"column::{table.name}.{col.name}"
            nodes.add(col_node)
            edges.append(GraphEdge(src=table_node, relation="包含字段", dst=col_node))
            if col.meaning:
                meaning_node = f"meaning::{table.name}.{col.name}"
                nodes.add(meaning_node)
                edges.append(GraphEdge(src=col_node, relation="含义", dst=meaning_node))
            col_buckets[_normalize_key(col.name)].append(table_node)

    # 同名/同义关键字段跨表连边，形成可用于社区发现的拓扑
    for key, table_nodes in col_buckets.items():
        if len(table_nodes) <= 1:
            continue
        deduped = sorted(set(table_nodes))
        for i in range(len(deduped)):
            for j in range(i + 1, len(deduped)):
                edges.append(GraphEdge(src=deduped[i], relation=f"共享键::{key}", dst=deduped[j]))

    return nodes, edges


def _find_communities(nodes: set[str], edges: list[GraphEdge]) -> list[list[str]]:
    """
    轻量社区划分：
    - 先按无向连通分量
    - 过滤过小社区
    """
    adj: dict[str, set[str]] = {n: set() for n in nodes}
    for e in edges:
        adj[e.src].add(e.dst)
        adj[e.dst].add(e.src)

    visited: set[str] = set()
    communities: list[list[str]] = []

    for node in nodes:
        if node in visited:
            continue
        stack = [node]
        visited.add(node)
        component: list[str] = []
        while stack:
            cur = stack.pop()
            component.append(cur)
            for nxt in adj[cur]:
                if nxt not in visited:
                    visited.add(nxt)
                    stack.append(nxt)
        if len(component) >= 3:
            communities.append(sorted(component))

    # 将孤立或极小节点合并为一个“其他”社区，避免召回丢失
    in_community = set().union(*[set(c) for c in communities]) if communities else set()
    leftovers = sorted(n for n in nodes if n not in in_community)
    if leftovers:
        communities.append(leftovers)

    return communities


def _community_summary(community_nodes: list[str], edges: list[GraphEdge]) -> str:
    """构建社区摘要（可被后续 LLM 汇总替换）。"""
    table_nodes = [n for n in community_nodes if n.startswith("table::")]
    column_nodes = [n for n in community_nodes if n.startswith("column::")]
    entity_nodes = [n for n in community_nodes if n.startswith("entity::")]
    edge_in = [e for e in edges if e.src in community_nodes and e.dst in community_nodes]

    top_tables = ", ".join(t.replace("table::", "") for t in table_nodes[:10]) or "无"
    top_entities = ", ".join(e.replace("entity::", "") for e in entity_nodes[:8]) or "无"
    rel_samples = "; ".join(f"{e.src} -[{e.relation}]-> {e.dst}" for e in edge_in[:8]) or "无"

    return (
        "该社区由一组高关联 schema 实体组成，适合作为 Text2SQL 的候选上下文。\n"
        f"- 表数量: {len(table_nodes)}\n"
        f"- 字段节点数量: {len(column_nodes)}\n"
        f"- 实体语义数量: {len(entity_nodes)}\n"
        f"- 代表表: {top_tables}\n"
        f"- 代表实体语义: {top_entities}\n"
        f"- 典型关系样例: {rel_samples}"
    )


def _table_document(table: TableInfo, community_id: int | None = None) -> Document:
    col_lines = "\n".join(
        f"- {c.name} ({c.col_type}): {c.meaning}" for c in table.columns[:80]
    ) or "- 无字段说明"
    text = (
        f"[TABLE]\n"
        f"表名: {table.name}\n"
        f"实体含义: {table.entity or '无'}\n"
        f"字段:\n{col_lines}"
    )
    metadata: dict[str, Any] = {
        "doc_type": "table",
        "table": table.name,
        "entity": table.entity,
    }
    if community_id is not None:
        metadata["community_id"] = community_id
    return Document(text=text, metadata=metadata)


def _community_document(community_id: int, summary: str, community_nodes: list[str]) -> Document:
    text = f"[COMMUNITY {community_id}]\n{summary}"
    tables = sorted(
        n.replace("table::", "")
        for n in community_nodes
        if n.startswith("table::")
    )
    return Document(
        text=text,
        metadata={
            "doc_type": "community_summary",
            "community_id": community_id,
            "tables": tables,
        },
    )


def load_schema_documents(schema_path: Path) -> list[Document]:
    """
    仅加载“表文档”（向后兼容旧调用）。
    GraphRAG 全量构建请使用 build_schema_graph。
    """
    content = schema_path.read_text(encoding="utf-8")
    tables = _parse_schema_markdown(content)
    return [_table_document(t) for t in tables]


def _create_pgvector_store():
    """
    按配置创建 PGVectorStore；未启用 PGVECTOR_URL 时返回 None。
    构建索引与从库加载索引共用同一套连接参数。
    """
    from text2sql.config import get_pgvector_config

    pg_cfg = get_pgvector_config()
    if not pg_cfg.get("enabled"):
        return None

    try:
        from llama_index.vector_stores.postgres import PGVectorStore
    except ImportError as exc:
        raise ImportError(
            "已配置 PGVECTOR_URL，但缺少依赖。请安装: "
            "llama-index-vector-stores-postgres、psycopg[binary]、pgvector"
        ) from exc

    db_url = make_url(pg_cfg["url"])
    # 兼容不同 SQLAlchemy URL 形式，显式拆分参数比 connection_string 更稳定。
    # 某些依赖组合会在内部把端口处理成字符串 "None"，导致 create_async_engine 解析失败。
    return PGVectorStore.from_params(
        host=db_url.host or "localhost",
        port=int(db_url.port or 5432),
        database=(db_url.database or "").lstrip("/"),
        user=db_url.username or "",
        password=db_url.password or "",
        table_name=pg_cfg["table_name"],
        schema_name=pg_cfg["schema_name"],
        embed_dim=pg_cfg["embed_dim"],
    )


def _build_storage_context_from_pgvector():
    store = _create_pgvector_store()
    if store is None:
        return None
    return StorageContext.from_defaults(vector_store=store)


_schema_index: Optional[VectorStoreIndex] = None
_schema_graph_artifacts: dict[str, Any] = {}


def build_schema_graph(
    schema_path: Optional[Path] = None,
    embed_model=None,
) -> VectorStoreIndex:
    """
    构建 GraphRAG：
    - 解析 schema 文档
    - 建图（实体关系）
    - 社区划分 + 社区摘要
    - 将“表文档 + 社区文档”向量化并写入 pgvector（若配置）
    """
    global _schema_index
    global _schema_graph_artifacts

    if schema_path is None:
        from text2sql.config import get_schema_description_path

        schema_path = get_schema_description_path()

    content = schema_path.read_text(encoding="utf-8")
    tables = _parse_schema_markdown(content)
    nodes, edges = _build_schema_graph(tables)
    communities = _find_communities(nodes, edges)

    # 记录每个 table 落在哪个社区
    table_to_community: dict[str, int] = {}
    for cid, c_nodes in enumerate(communities):
        for n in c_nodes:
            if n.startswith("table::"):
                table_to_community[n.replace("table::", "")] = cid

    docs: list[Document] = []
    for t in tables:
        docs.append(_table_document(t, community_id=table_to_community.get(t.name)))
    for cid, c_nodes in enumerate(communities):
        docs.append(_community_document(cid, _community_summary(c_nodes, edges), c_nodes))

    storage_context = _build_storage_context_from_pgvector()
    try:
        if embed_model is not None:
            index = VectorStoreIndex.from_documents(
                docs,
                embed_model=embed_model,
                storage_context=storage_context,
            )
        else:
            index = VectorStoreIndex.from_documents(
                docs,
                storage_context=storage_context,
            )
    except Exception as exc:
        msg = str(exc)
        if "expected" in msg and "dimensions" in msg and "not" in msg:
            raise RuntimeError(
                "pgvector 向量维度与 Embedding 模型不一致。"
                "请检查 .env 中 PGVECTOR_EMBED_DIM 是否与 EMBEDDING_MODEL 匹配。"
                "例如：text-embedding-3-small=1536，text-embedding-3-large=3072。"
                "若历史表已按旧维度创建，请更换 PGVECTOR_TABLE 或清理旧表后重建。"
            ) from exc
        raise

    _schema_index = index
    _schema_graph_artifacts = {
        "tables": tables,
        "nodes": nodes,
        "edges": edges,
        "communities": communities,
        "table_to_community": table_to_community,
        "doc_count": len(docs),
    }
    return index


def load_schema_index_from_pgvector(embed_model=None) -> VectorStoreIndex:
    """
    从已持久化的 pgvector 表加载向量索引（不解析 Markdown、不重建图）。

    适用于先执行 ``main.py --build-graphrag-only`` 写入向量后，
    在 ``--testModel`` 等场景仅做向量检索。
    """
    global _schema_index
    global _schema_graph_artifacts

    from llama_index.core import Settings

    store = _create_pgvector_store()
    if store is None:
        raise RuntimeError(
            "未配置 PGVECTOR_URL，无法从 pgvector 加载已构建的索引。"
            "请先在 .env 中配置 PGVECTOR_*，执行: python main.py --build-graphrag-only，"
            "再使用 --testModel。"
        )

    emb = embed_model if embed_model is not None else Settings.embed_model
    if emb is None:
        raise RuntimeError("未设置 Settings.embed_model，无法对查询做向量化。请先 init_llm_and_embedding()。")

    index = VectorStoreIndex.from_vector_store(vector_store=store, embed_model=emb)
    _schema_index = index
    _schema_graph_artifacts = {}
    return index


def get_schema_graph_index() -> Optional[VectorStoreIndex]:
    return _schema_index


def get_schema_graph_artifacts() -> dict[str, Any]:
    """返回 GraphRAG 中间产物，用于调试/可视化。"""
    return _schema_graph_artifacts


def get_schema_retriever(top_k: int = 5):
    idx = get_schema_graph_index()
    if idx is None:
        raise RuntimeError("请先调用 build_schema_graph() 构建 schema GraphRAG 索引")
    return idx.as_retriever(similarity_top_k=top_k)


def format_retrieved_schema_context(retriever, question: str) -> str:
    """
    使用已构建的 GraphRAG 向量索引，按问题做一次真实向量检索，
    将命中的节点文本拼接为供 LLM 使用的 schema 上下文（非全量 Markdown）。
    """
    results = retriever.retrieve(question)
    if not results:
        return ""

    parts: list[str] = []
    for i, node_with_score in enumerate(results, start=1):
        score = getattr(node_with_score, "score", None)
        meta = getattr(node_with_score.node, "metadata", None) or {}
        doc_type = meta.get("doc_type", "")
        header_bits = [f"片段 {i}"]
        if score is not None:
            header_bits.append(f"相似度 {score:.4f}")
        if doc_type:
            header_bits.append(f"type={doc_type}")
        if meta.get("table"):
            header_bits.append(f"table={meta['table']}")
        if meta.get("community_id") is not None:
            header_bits.append(f"community_id={meta['community_id']}")

        header = " | ".join(header_bits)
        body = node_with_score.get_content()
        parts.append(f"### {header}\n{body}")

    preamble = (
        "以下是与当前问题最相关的 schema 片段（由向量检索得到，非完整库表说明）。"
        "生成 SQL 时请优先依据这些表与字段；若信息不足请明确说明缺什么。\n\n"
    )
    return preamble + "\n\n".join(parts)
