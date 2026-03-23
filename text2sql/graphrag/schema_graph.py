"""
从 schema 描述 Markdown 构建 GraphRAG。

构建流程（参考标准 GraphRAG）：
1) 文本分块 (create_base_text_units)
2) 实体与关系提取 (extract_graph) — 使用 LLM
3) 描述总结与去重 — 使用 LLM
4) 图的最终形成 (finalize_graph) — 使用 NetworkX
5) 社区检测 + 社区摘要 — Louvain + LLM
6) 文档向量化
"""
from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from llama_index.core import Document, StorageContext, VectorStoreIndex
from sqlalchemy.engine import make_url


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Step 1: 文本分块 (create_base_text_units)
# ---------------------------------------------------------------------------

def _create_text_chunks(
    content: str,
    chunk_size: int | None = None,
    overlap: int | None = None,
) -> list[dict[str, Any]]:
    """
    将原始文档按固定大小分块，每块带唯一 ID。
    尽量在换行符处断开，避免切断一行。
    """
    chunk_size = chunk_size or int(os.getenv("GRAPHRAG_CHUNK_SIZE", "1200"))
    overlap = overlap or int(os.getenv("GRAPHRAG_CHUNK_OVERLAP", "200"))

    chunks: list[dict[str, Any]] = []
    start = 0
    chunk_id = 0
    while start < len(content):
        end = min(start + chunk_size, len(content))
        if end < len(content):
            nl = content.rfind("\n", start + chunk_size // 2, end)
            if nl > start:
                end = nl + 1
        text = content[start:end].strip()
        if text:
            chunks.append({"id": f"chunk_{chunk_id}", "text": text, "start": start, "end": end})
            chunk_id += 1
        start = end - overlap if end < len(content) else len(content)
    return chunks


# ---------------------------------------------------------------------------
# Step 2: 实体与关系提取 (extract_graph)
# ---------------------------------------------------------------------------

_EXTRACT_PROMPT = """你是数据库 schema 分析专家。请从以下文本中提取所有实体（Entities）和关系（Relationships）。

实体类型包括但不限于：
- Table：数据库表
- Column：字段/列
- BusinessConcept：业务概念（如玩家、道具、钻石、服务器等）
- Metric：数据指标（如等级、数量、时间等）

关系类型包括但不限于：
- CONTAINS：表包含字段
- DESCRIBES：表描述某业务概念
- REFERENCES：字段引用其他表
- SHARED_KEY：表之间通过共享键关联
- MEASURES：字段度量某指标

请严格按以下 JSON 格式输出，不要输出其他内容：
{
  "entities": [
    {"name": "实体名称", "type": "实体类型", "description": "简要描述"}
  ],
  "relationships": [
    {"source": "源实体", "target": "目标实体", "relation": "关系类型", "description": "简要描述"}
  ]
}

文本：
{text}"""


def _parse_llm_json(raw: str) -> dict:
    """从 LLM 返回中提取 JSON，兼容 markdown 代码块包裹。"""
    text = raw.strip()
    if "```" in text:
        for part in text.split("```"):
            part = part.strip()
            if part.lower().startswith("json"):
                part = part[4:].strip()
            if part.startswith("{"):
                text = part
                break
    return json.loads(text)


def _extract_graph(chunks: list[dict[str, Any]]) -> tuple[list[dict], list[dict]]:
    """
    Step 2: 调用 LLM 从每个文本块中提取实体和关系。
    返回 (raw_entities, raw_relations)。
    """
    from llama_index.core import Settings

    all_entities: list[dict] = []
    all_relations: list[dict] = []

    for i, chunk in enumerate(chunks, 1):
        print(f"  [抽取] 块 {i}/{len(chunks)} ({chunk['id']})...")
        prompt = _EXTRACT_PROMPT.replace("{text}", chunk["text"])
        try:
            resp = Settings.llm.complete(prompt)
            text = str(getattr(resp, "text", "") or resp)
            data = _parse_llm_json(text)
            for e in data.get("entities", []):
                e["source_chunk"] = chunk["id"]
                all_entities.append(e)
            for r in data.get("relationships", []):
                r["source_chunk"] = chunk["id"]
                all_relations.append(r)
        except Exception as exc:
            print(f"  警告: {chunk['id']} 抽取失败: {exc}")

    print(f"  抽取完成: {len(all_entities)} 个实体, {len(all_relations)} 个关系")
    return all_entities, all_relations


# ---------------------------------------------------------------------------
# Step 3: 描述总结与去重
# ---------------------------------------------------------------------------

_SUMMARIZE_ENTITY_PROMPT = """以下是关于实体「{name}」（类型: {type}）在不同文本块中的多段描述。
请将它们汇总为一段全面、准确的最终描述（中文，50-150字）。

各段描述：
{descriptions}

请直接输出汇总后的描述，不要 JSON 包裹。"""

_SUMMARIZE_RELATION_PROMPT = """以下是关于「{source}」与「{target}」之间关系的多段描述。
请汇总为一段简洁、准确的关系描述（中文，30-100字）。

各段描述：
{descriptions}

请直接输出汇总后的描述，不要 JSON 包裹。"""


def _deduplicate_and_summarize(
    raw_entities: list[dict],
    raw_relations: list[dict],
) -> tuple[list[dict], list[dict]]:
    """
    Step 3: 对同名同类型实体、同源-目标关系去重，
    若同一实体/关系出现多次则用 LLM 汇总描述。
    """
    from llama_index.core import Settings

    # ---- 实体去重 ----
    entity_groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for e in raw_entities:
        name = (e.get("name") or "").strip()
        etype = (e.get("type") or "").strip()
        if name:
            entity_groups[(name, etype)].append(e)

    entities: list[dict] = []
    total_to_summarize = sum(1 for g in entity_groups.values() if len(g) > 1)
    summarized = 0
    for (name, etype), group in entity_groups.items():
        descriptions = [e.get("description", "") for e in group if e.get("description")]
        source_chunks = sorted(set(e.get("source_chunk", "") for e in group))

        if len(descriptions) > 1:
            summarized += 1
            print(f"  [去重] 汇总实体 {summarized}/{total_to_summarize}: {name}")
            try:
                prompt = (
                    _SUMMARIZE_ENTITY_PROMPT
                    .replace("{name}", name)
                    .replace("{type}", etype)
                    .replace("{descriptions}", "\n".join(f"- {d}" for d in descriptions))
                )
                resp = Settings.llm.complete(prompt)
                final_desc = str(getattr(resp, "text", "") or resp).strip()
            except Exception:
                final_desc = " | ".join(descriptions)
        else:
            final_desc = descriptions[0] if descriptions else ""

        entities.append({
            "name": name,
            "type": etype,
            "description": final_desc,
            "source_chunks": source_chunks,
        })

    # ---- 关系去重 ----
    relation_groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in raw_relations:
        src = (r.get("source") or "").strip()
        tgt = (r.get("target") or "").strip()
        if src and tgt:
            relation_groups[(src, tgt)].append(r)

    relations: list[dict] = []
    total_rel_summarize = sum(1 for g in relation_groups.values() if len(g) > 1)
    summarized_rel = 0
    for (src, tgt), group in relation_groups.items():
        descriptions = [r.get("description", "") for r in group if r.get("description")]
        rel_types = sorted(set(r.get("relation", "") for r in group if r.get("relation")))
        source_chunks = sorted(set(r.get("source_chunk", "") for r in group))

        if len(descriptions) > 1:
            summarized_rel += 1
            print(f"  [去重] 汇总关系 {summarized_rel}/{total_rel_summarize}: {src} -> {tgt}")
            try:
                prompt = (
                    _SUMMARIZE_RELATION_PROMPT
                    .replace("{source}", src)
                    .replace("{target}", tgt)
                    .replace("{descriptions}", "\n".join(f"- {d}" for d in descriptions))
                )
                resp = Settings.llm.complete(prompt)
                final_desc = str(getattr(resp, "text", "") or resp).strip()
            except Exception:
                final_desc = " | ".join(descriptions)
        else:
            final_desc = descriptions[0] if descriptions else ""

        relations.append({
            "source": src,
            "target": tgt,
            "relation": rel_types[0] if rel_types else "RELATED",
            "description": final_desc,
            "source_chunks": source_chunks,
        })

    print(f"  去重完成: {len(entities)} 个实体, {len(relations)} 个关系")
    return entities, relations


# ---------------------------------------------------------------------------
# Step 4: 图的最终形成 (finalize_graph)
# ---------------------------------------------------------------------------

def _finalize_graph(entities: list[dict], relations: list[dict]):
    """
    Step 4: 使用 NetworkX 构建最终图，计算节点度等结构信息。
    返回 networkx.Graph。
    """
    import networkx as nx

    G = nx.Graph()

    for e in entities:
        G.add_node(
            e["name"],
            type=e["type"],
            description=e["description"],
            source_chunks=e.get("source_chunks", []),
        )

    for r in relations:
        src, tgt = r["source"], r["target"]
        if not G.has_node(src):
            G.add_node(src, type="Unknown", description="", source_chunks=[])
        if not G.has_node(tgt):
            G.add_node(tgt, type="Unknown", description="", source_chunks=[])
        G.add_edge(
            src, tgt,
            relation=r["relation"],
            description=r["description"],
            source_chunks=r.get("source_chunks", []),
        )

    for node in G.nodes():
        G.nodes[node]["degree"] = G.degree(node)

    print(f"  图构建完成: {G.number_of_nodes()} 节点, {G.number_of_edges()} 边")
    return G


# ---------------------------------------------------------------------------
# 社区检测 + LLM 社区摘要
# ---------------------------------------------------------------------------

def _detect_communities(G) -> list[list[str]]:
    """使用 Louvain 算法进行社区检测，回退到连通分量。"""
    import networkx as nx

    if G.number_of_nodes() == 0:
        return []

    try:
        raw = nx.community.louvain_communities(G, seed=42)
        communities = [sorted(c) for c in raw if len(c) >= 2]
    except Exception:
        communities = [sorted(c) for c in nx.connected_components(G) if len(c) >= 2]

    in_community = set().union(*(set(c) for c in communities)) if communities else set()
    leftovers = sorted(n for n in G.nodes() if n not in in_community)
    if leftovers:
        communities.append(leftovers)

    print(f"  社区检测完成: {len(communities)} 个社区")
    return communities


_COMMUNITY_SUMMARY_PROMPT = """你是数据库 schema 分析专家。以下是一组高度关联的数据库实体和关系，构成一个主题社区。
请为这个社区生成一段简洁的摘要（中文，100-300字），包含：
1. 核心业务主题
2. 主要数据库表和字段
3. 关键关联关系
4. 适合回答哪类 Text2SQL 问题

实体列表：
{entities}

关系列表：
{relations}

请直接输出摘要文本。"""


def _generate_community_summaries(communities: list[list[str]], G) -> dict[int, str]:
    """为每个社区调用 LLM 生成摘要。"""
    from llama_index.core import Settings

    summaries: dict[int, str] = {}
    community_set = {frozenset(c) for c in communities}

    for cid, members in enumerate(communities):
        member_set = set(members)

        entities_lines: list[str] = []
        for n in members[:30]:
            data = G.nodes.get(n, {})
            entities_lines.append(
                f"- {n} (类型: {data.get('type', '?')}, "
                f"度: {data.get('degree', 0)}): {data.get('description', '')}"
            )

        relations_lines: list[str] = []
        for u, v, data in G.edges(data=True):
            if u in member_set and v in member_set:
                relations_lines.append(
                    f"- {u} -[{data.get('relation', '')}]-> {v}: {data.get('description', '')}"
                )
        relations_lines = relations_lines[:20]

        print(f"  [摘要] 社区 {cid + 1}/{len(communities)} "
              f"({len(members)} 实体, {len(relations_lines)} 关系)...")

        prompt = (
            _COMMUNITY_SUMMARY_PROMPT
            .replace("{entities}", "\n".join(entities_lines))
            .replace("{relations}", "\n".join(relations_lines) or "无")
        )
        try:
            resp = Settings.llm.complete(prompt)
            summaries[cid] = str(getattr(resp, "text", "") or resp).strip()
        except Exception:
            tables = [n for n in members if G.nodes.get(n, {}).get("type") == "Table"]
            summaries[cid] = (
                f"该社区包含 {len(members)} 个实体。"
                f"主要表: {', '.join(tables[:10]) or '无'}。"
            )

    return summaries


# ---------------------------------------------------------------------------
# 解析 schema Markdown（仍用于表文档创建）
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# 文档创建
# ---------------------------------------------------------------------------

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
        n for n in community_nodes
        # 社区节点中可能既有 Table 类型的实体名，也有其他类型
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
    """仅加载"表文档"（向后兼容旧调用）。"""
    content = schema_path.read_text(encoding="utf-8")
    tables = _parse_schema_markdown(content)
    return [_table_document(t) for t in tables]


# ---------------------------------------------------------------------------
# pgvector 存储
# ---------------------------------------------------------------------------

def _create_pgvector_store():
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


# ---------------------------------------------------------------------------
# 全局状态
# ---------------------------------------------------------------------------

_schema_index: Optional[VectorStoreIndex] = None
_schema_graph_artifacts: dict[str, Any] = {}


# ---------------------------------------------------------------------------
# 主入口：构建 GraphRAG
# ---------------------------------------------------------------------------

def build_schema_graph(
    schema_path: Optional[Path] = None,
    embed_model=None,
) -> VectorStoreIndex:
    """
    标准 GraphRAG 构建流程：
    1) 文本分块
    2) LLM 实体/关系抽取
    3) 去重 + LLM 描述汇总
    4) NetworkX 建图
    5) 社区检测 + LLM 社区摘要
    6) 文档向量化
    """
    global _schema_index
    global _schema_graph_artifacts

    if schema_path is None:
        from text2sql.config import get_schema_description_path
        schema_path = get_schema_description_path()

    content = schema_path.read_text(encoding="utf-8")

    # ---- Step 1: 文本分块 ----
    print("Step 1/6: 文本分块...")
    chunks = _create_text_chunks(content)
    print(f"  分块完成: {len(chunks)} 个文本块")

    # ---- Step 2: LLM 实体/关系抽取 ----
    print("Step 2/6: LLM 实体/关系抽取...")
    raw_entities, raw_relations = _extract_graph(chunks)

    # ---- Step 3: 去重 + 描述汇总 ----
    print("Step 3/6: 去重与描述汇总...")
    entities, relations = _deduplicate_and_summarize(raw_entities, raw_relations)

    # ---- Step 4: NetworkX 建图 ----
    print("Step 4/6: 构建 NetworkX 图...")
    G = _finalize_graph(entities, relations)

    # ---- Step 5: 社区检测 + 社区摘要 ----
    print("Step 5/6: 社区检测与摘要生成...")
    communities = _detect_communities(G)
    community_summaries = _generate_community_summaries(communities, G)

    # 映射：哪些 Table 类型实体落在哪个社区
    table_to_community: dict[str, int] = {}
    for cid, members in enumerate(communities):
        for n in members:
            node_data = G.nodes.get(n, {})
            if node_data.get("type") == "Table":
                table_to_community[n] = cid

    # ---- Step 5.5: 嵌入实体描述向量（供局部检索用） ----
    print("  嵌入实体描述向量...")
    from llama_index.core import Settings as _Settings
    _emb = embed_model or _Settings.embed_model
    entity_embeddings: dict[str, list[float]] = {}
    if _emb is not None:
        entity_texts = [e.get("description") or e["name"] for e in entities]
        entity_name_list = [e["name"] for e in entities]
        try:
            vecs = _emb.get_text_embedding_batch(entity_texts)
            entity_embeddings = dict(zip(entity_name_list, vecs))
        except Exception:
            for name, txt in zip(entity_name_list, entity_texts):
                try:
                    entity_embeddings[name] = _emb.get_text_embedding(txt)
                except Exception:
                    pass
    print(f"  已嵌入 {len(entity_embeddings)}/{len(entities)} 个实体向量")

    # 构建 table 文档文本映射（供局部检索拼接 SQL 上下文用）
    tables = _parse_schema_markdown(content)
    table_doc_map: dict[str, str] = {}
    table_meta_map: dict[str, dict] = {}
    for t in tables:
        doc = _table_document(t)
        table_doc_map[t.name] = doc.text
        table_meta_map[t.name] = doc.metadata

    # ---- Step 6: 文档向量化 ----
    print("Step 6/6: 创建文档并向量化...")

    # 用社区映射为 table 文档打标签（表名可能与图中实体名不完全一致，做模糊匹配）
    entity_names = {e["name"] for e in entities}
    table_community_map: dict[str, int | None] = {}
    for t in tables:
        if t.name in table_to_community:
            table_community_map[t.name] = table_to_community[t.name]
        else:
            for ename, cid in table_to_community.items():
                if t.name.lower() == ename.lower():
                    table_community_map[t.name] = cid
                    break
            else:
                table_community_map[t.name] = None

    docs: list[Document] = []
    for t in tables:
        docs.append(_table_document(t, community_id=table_community_map.get(t.name)))
    for cid, members in enumerate(communities):
        summary = community_summaries.get(cid, "")
        docs.append(_community_document(cid, summary, members))

    storage_context = _build_storage_context_from_pgvector()
    try:
        kwargs: dict[str, Any] = {}
        if embed_model is not None:
            kwargs["embed_model"] = embed_model
        if storage_context is not None:
            kwargs["storage_context"] = storage_context
        index = VectorStoreIndex.from_documents(docs, **kwargs)
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

    # 将 GraphEdge 风格的边也保存，供其他模块消费
    graph_edges: list[GraphEdge] = []
    for u, v, data in G.edges(data=True):
        graph_edges.append(GraphEdge(src=u, relation=data.get("relation", ""), dst=v))

    _schema_index = index
    _schema_graph_artifacts = {
        "tables": tables,
        "nodes": set(G.nodes()),
        "edges": graph_edges,
        "communities": communities,
        "table_to_community": table_community_map,
        "doc_count": len(docs),
        "entities": entities,
        "relations": relations,
        "community_summaries": community_summaries,
        "chunks": chunks,
        "nx_graph": G,
        "entity_embeddings": entity_embeddings,
        "table_documents": table_doc_map,
        "table_metadata": table_meta_map,
    }

    # 自动落盘 artifacts（含 entity_embeddings），供 --testModel 跨进程加载
    cache_path = save_artifacts_to_local(_schema_graph_artifacts)
    print(f"GraphRAG artifacts 已保存到: {cache_path}")

    print(
        f"GraphRAG 构建完成: "
        f"chunks={len(chunks)}, entities={len(entities)}, relations={len(relations)}, "
        f"nodes={G.number_of_nodes()}, edges={G.number_of_edges()}, "
        f"communities={len(communities)}, docs={len(docs)}"
    )
    return index


# ---------------------------------------------------------------------------
# 索引加载 / 访问器 / 检索
# ---------------------------------------------------------------------------

def load_schema_index_from_pgvector(embed_model=None) -> VectorStoreIndex:
    """从已持久化的 pgvector 表加载向量索引（不重建图）。"""
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


# ---------------------------------------------------------------------------
# artifacts 本地持久化
# ---------------------------------------------------------------------------

def _default_artifacts_path() -> Path:
    return Path(__file__).resolve().parents[2] / "graphrag_data" / "artifacts.json"


def save_artifacts_to_local(
    artifacts: dict[str, Any],
    path: Path | None = None,
) -> Path:
    """
    将构建产物（含 entity_embeddings）序列化为 JSON 存储到本地。
    NetworkX 图对象不序列化，加载时可按 entities/relations 重建。
    """
    import json
    from dataclasses import asdict

    path = path or _default_artifacts_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    serializable: dict[str, Any] = {
        "version": 2,
        "entities": artifacts["entities"],
        "relations": artifacts["relations"],
        "communities": artifacts["communities"],
        "community_summaries": {
            str(k): v for k, v in artifacts["community_summaries"].items()
        },
        "chunks": artifacts.get("chunks", []),
        "table_to_community": artifacts.get("table_to_community", {}),
        "entity_embeddings": artifacts.get("entity_embeddings", {}),
        "table_documents": artifacts.get("table_documents", {}),
        "table_metadata": artifacts.get("table_metadata", {}),
        "tables": [asdict(t) for t in artifacts.get("tables", [])],
        "edges": [asdict(e) for e in artifacts.get("edges", [])],
    }
    path.write_text(
        json.dumps(serializable, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def load_artifacts_from_local(path: Path | None = None) -> dict[str, Any]:
    """
    从本地 JSON 文件恢复 artifacts（含 entity_embeddings），
    并按 entities/relations 重建 NetworkX 图。
    """
    import json

    path = path or _default_artifacts_path()
    if not path.exists():
        raise FileNotFoundError(f"本地 GraphRAG artifacts 不存在: {path}")

    raw = json.loads(path.read_text(encoding="utf-8"))

    tables = [
        TableInfo(
            name=t["name"],
            entity=t["entity"],
            columns=[ColumnInfo(**c) for c in t["columns"]],
        )
        for t in raw.get("tables", [])
    ]
    edges = [GraphEdge(**e) for e in raw.get("edges", [])]
    community_summaries = {int(k): v for k, v in raw.get("community_summaries", {}).items()}

    # 按 entities/relations 重建 NetworkX 图
    G = _finalize_graph(raw["entities"], raw["relations"])

    return {
        "tables": tables,
        "nodes": set(G.nodes()),
        "edges": edges,
        "communities": raw["communities"],
        "table_to_community": raw.get("table_to_community", {}),
        "entities": raw["entities"],
        "relations": raw["relations"],
        "community_summaries": community_summaries,
        "chunks": raw.get("chunks", []),
        "nx_graph": G,
        "entity_embeddings": raw.get("entity_embeddings", {}),
        "table_documents": raw.get("table_documents", {}),
        "table_metadata": raw.get("table_metadata", {}),
    }
