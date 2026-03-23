"""
GraphRAG 局部检索（Local Search）。

标准流程：
1) 检索相关实体：用户查询向量化，与所有实体描述做相似度匹配，取 Top-K
2) 构建候选集：候选社区 / 候选关系 / 候选文本单元
3) 上下文排序与筛选：社区、关系、文本单元分别按精细规则排序
4) 拼接上下文：社区报告 + 实体描述 + 关系描述 + 文本单元 + 表文档
"""
from __future__ import annotations

import math
import os
import re
from collections import defaultdict
from typing import Any, Optional

from llama_index.core.schema import NodeWithScore, TextNode


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


# ---------------------------------------------------------------------------
# 局部检索器
# ---------------------------------------------------------------------------

class GraphRAGLocalSearch:
    """
    标准 GraphRAG 局部检索。

    对外接口 ``retrieve(question)`` 返回 ``list[NodeWithScore]``，
    可直接被 ``format_retrieved_schema_context`` 消费。
    """

    def __init__(
        self,
        artifacts: dict[str, Any],
        top_k_entities: int | None = None,
        context_budget: int | None = None,
    ):
        self.entities: list[dict] = artifacts["entities"]
        self.relations: list[dict] = artifacts["relations"]
        self.communities: list[list[str]] = artifacts["communities"]
        self.community_summaries: dict[int, str] = artifacts["community_summaries"]
        self.chunks: list[dict] = artifacts.get("chunks", [])
        self.nx_graph = artifacts.get("nx_graph")
        self.entity_embeddings: dict[str, list[float]] = artifacts.get("entity_embeddings", {})
        self.table_documents: dict[str, str] = artifacts.get("table_documents", {})
        self.table_metadata: dict[str, dict] = artifacts.get("table_metadata", {})

        self.top_k_entities = top_k_entities or int(os.getenv("GRAPHRAG_TOP_K_ENTITIES", "10"))
        self.context_budget = context_budget or int(os.getenv("GRAPHRAG_CONTEXT_BUDGET", "15"))
        # SQL-first: table 文档至少占上下文 70%
        self.table_ratio = float(os.getenv("GRAPHRAG_TABLE_CONTEXT_RATIO", "0.7"))
        # SQL 信号与语义信号的融合权重
        self.sql_signal_weight = float(os.getenv("GRAPHRAG_SQL_SIGNAL_WEIGHT", "0.45"))

        # ---- 预构建索引 ----
        self._entity_map: dict[str, dict] = {e["name"]: e for e in self.entities}

        # 实体 -> 社区列表
        self._entity_to_communities: dict[str, list[int]] = defaultdict(list)
        for cid, members in enumerate(self.communities):
            for name in members:
                self._entity_to_communities[name].append(cid)

        # 实体 -> 文本块 ID 列表
        self._entity_to_chunks: dict[str, list[str]] = {}
        for e in self.entities:
            self._entity_to_chunks[e["name"]] = e.get("source_chunks", [])

        # 实体 -> 关联关系列表
        self._entity_to_relations: dict[str, list[dict]] = defaultdict(list)
        for r in self.relations:
            self._entity_to_relations[r["source"]].append(r)
            self._entity_to_relations[r["target"]].append(r)

        # 文本块 ID -> 文本块
        self._chunk_map: dict[str, dict] = {c["id"]: c for c in self.chunks}
        # 表名小写映射，便于实体名与真实表名对齐
        self._table_lc_map = {name.lower(): name for name in self.table_documents}

    # ------------------------------------------------------------------
    # 步骤 1：检索相关实体
    # ------------------------------------------------------------------

    def _find_candidate_entities(
        self, query_embedding: list[float],
    ) -> list[tuple[str, float]]:
        """
        将用户查询嵌入与所有实体描述向量做余弦相似度，
        返回 Top-K 候选实体 [(entity_name, similarity_score), ...]。
        """
        scores: list[tuple[str, float]] = []
        for name, vec in self.entity_embeddings.items():
            sim = _cosine(query_embedding, vec)
            scores.append((name, sim))
        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[: self.top_k_entities]

    def _tokenize_query(self, text: str) -> set[str]:
        tokens = set(re.findall(r"[a-zA-Z_]\w+|[\u4e00-\u9fff]{2,}", text.lower()))
        # 拆 snake/camel 的弱版，提升字段命中率
        extra: set[str] = set()
        for t in tokens:
            if "_" in t:
                extra.update([p for p in t.split("_") if len(p) > 1])
        return tokens | extra

    def _table_sql_signal_score(self, question: str, table_name: str) -> float:
        """
        SQL 导向信号打分（与语义相似度融合）：
        - 表名命中
        - 字段名命中
        - 常见 join key 命中
        """
        q = question.lower()
        q_tokens = self._tokenize_query(question)
        meta = self.table_metadata.get(table_name, {})
        signal = 0.0

        # 表名/别名命中
        t_lc = table_name.lower()
        if t_lc in q:
            signal += 1.0
        for part in [p for p in t_lc.split("_") if len(p) > 1]:
            if part in q_tokens:
                signal += 0.15

        # 字段名命中（从 table 文档文本中粗提取）
        doc = self.table_documents.get(table_name, "")
        col_tokens = set(re.findall(r"-\s*([a-zA-Z_]\w*)\s*\(", doc))
        for c in col_tokens:
            c_lc = c.lower()
            if c_lc in q_tokens:
                signal += 0.25
            for part in [p for p in c_lc.split("_") if len(p) > 1]:
                if part in q_tokens:
                    signal += 0.08

        # join key 强信号
        join_keys = {"user_id", "userid", "uid", "server_id", "svrid", "role_id", "time", "date"}
        if col_tokens & join_keys and q_tokens & join_keys:
            signal += 0.6

        # 元数据中的实体描述命中
        entity = str(meta.get("entity", "")).lower()
        if entity and any(tok in entity for tok in q_tokens):
            signal += 0.2

        return signal

    # ------------------------------------------------------------------
    # 步骤 2：构建候选集
    # ------------------------------------------------------------------

    def _build_candidate_sets(
        self,
        candidate_entities: list[tuple[str, float]],
    ) -> tuple[set[int], list[dict], set[str]]:
        """
        返回 (候选社区ID集合, 候选关系列表, 候选文本块ID集合)。
        """
        candidate_names = {name for name, _ in candidate_entities}

        # 候选社区：包含至少一个候选实体的社区
        candidate_communities: set[int] = set()
        for name in candidate_names:
            for cid in self._entity_to_communities.get(name, []):
                candidate_communities.add(cid)

        # 候选关系：以候选实体为源或目标的边
        candidate_relations: list[dict] = []
        seen: set[tuple[str, str]] = set()
        for name in candidate_names:
            for r in self._entity_to_relations.get(name, []):
                key = (r["source"], r["target"])
                if key not in seen:
                    seen.add(key)
                    candidate_relations.append(r)

        # 候选文本单元：包含至少一个候选实体的原始文本块
        candidate_chunks: set[str] = set()
        for name in candidate_names:
            for chunk_id in self._entity_to_chunks.get(name, []):
                candidate_chunks.add(chunk_id)

        return candidate_communities, candidate_relations, candidate_chunks

    # ------------------------------------------------------------------
    # 步骤 3：上下文排序与筛选
    # ------------------------------------------------------------------

    def _rank_communities(
        self,
        candidate_community_ids: set[int],
        candidate_names: set[str],
    ) -> list[tuple[int, float]]:
        """
        社区排序规则：
        - 按社区内候选实体出现的文本单元数量 (matches) 降序
        - 若相同，按社区大小 (rank / importance) 降序
        """
        scored: list[tuple[int, float]] = []
        for cid in candidate_community_ids:
            if cid >= len(self.communities):
                continue
            members = self.communities[cid]
            # matches = 社区内候选实体命中的文本块总数
            matches = 0
            for m in members:
                if m in candidate_names:
                    matches += len(self._entity_to_chunks.get(m, []))
            rank = len(members)
            score = matches * 1000 + rank
            scored.append((cid, score))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored

    def _rank_relations(
        self,
        candidate_relations: list[dict],
        candidate_names: set[str],
    ) -> list[tuple[dict, float]]:
        """
        关系排序规则：
        - 网络内关系（连接两个候选实体）优先于网络外关系
        - 网络内按 combined_degree（源+目标节点度之和）降序
        - 网络外按连接到网络内实体的数量降序
        """
        in_network: list[tuple[dict, float]] = []
        out_network: list[tuple[dict, float]] = []

        for r in candidate_relations:
            src_in = r["source"] in candidate_names
            tgt_in = r["target"] in candidate_names
            src_degree = self._node_degree(r["source"])
            tgt_degree = self._node_degree(r["target"])

            if src_in and tgt_in:
                combined = src_degree + tgt_degree
                in_network.append((r, combined))
            else:
                other = r["target"] if src_in else r["source"]
                connections = sum(
                    1 for rel in self._entity_to_relations.get(other, [])
                    if rel["source"] in candidate_names or rel["target"] in candidate_names
                )
                out_network.append((r, connections))

        in_network.sort(key=lambda x: x[1], reverse=True)
        out_network.sort(key=lambda x: x[1], reverse=True)
        return in_network + out_network

    def _rank_text_units(
        self,
        candidate_chunk_ids: set[str],
        candidate_scores: dict[str, float],
    ) -> list[tuple[str, float]]:
        """
        文本单元排序规则：
        - 首先按关联候选实体中最高语义相似度排序
        - 其次按关联关系数量排序
        """
        scored: list[tuple[str, float]] = []
        for chunk_id in candidate_chunk_ids:
            max_sim = 0.0
            rel_count = 0
            for e in self.entities:
                if chunk_id in e.get("source_chunks", []):
                    sim = candidate_scores.get(e["name"], 0.0)
                    max_sim = max(max_sim, sim)
                    rel_count += len(self._entity_to_relations.get(e["name"], []))
            score = max_sim * 1000 + rel_count
            scored.append((chunk_id, score))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored

    def _node_degree(self, name: str) -> int:
        if self.nx_graph is not None and self.nx_graph.has_node(name):
            return self.nx_graph.nodes[name].get("degree", self.nx_graph.degree(name))
        return 0

    # ------------------------------------------------------------------
    # 步骤 4：拼接上下文并返回
    # ------------------------------------------------------------------

    def retrieve(self, question: str) -> list[NodeWithScore]:
        """
        标准 GraphRAG 局部检索主流程。

        返回排序后的 NodeWithScore 列表，包含：
        社区摘要 + 表文档 + 实体描述 + 关系描述 + 文本单元。
        """
        from llama_index.core import Settings

        emb = Settings.embed_model
        if emb is None or not self.entity_embeddings:
            return []

        query_vec = emb.get_query_embedding(question)

        # ---- 1) 检索相关实体 ----
        candidate_entities = self._find_candidate_entities(query_vec)
        if not candidate_entities:
            return []

        candidate_names = {name for name, _ in candidate_entities}
        candidate_scores = {name: score for name, score in candidate_entities}

        # ---- 2) 构建候选集 ----
        cand_communities, cand_relations, cand_chunks = self._build_candidate_sets(
            candidate_entities
        )

        # ---- 3) 排序 ----
        ranked_communities = self._rank_communities(cand_communities, candidate_names)
        ranked_relations = self._rank_relations(cand_relations, candidate_names)
        ranked_chunks = self._rank_text_units(cand_chunks, candidate_scores)

        # ---- 4) 按预算分配拼接结果 ----
        budget = self.context_budget
        results: list[NodeWithScore] = []
        table_budget = max(1, int(math.ceil(budget * self.table_ratio)))
        aux_budget = max(0, budget - table_budget)

        # 4a) SQL-first 表文档主召回（融合语义 + SQL 信号）
        matched_tables: list[tuple[str, float, float, float]] = []
        for name, sim in candidate_entities:
            e = self._entity_map.get(name, {})
            if e.get("type") == "Table" and name in self.table_documents:
                sql_signal = self._table_sql_signal_score(question, name)
                final = (1 - self.sql_signal_weight) * sim + self.sql_signal_weight * min(sql_signal, 2.0)
                matched_tables.append((name, final, sim, sql_signal))
            else:
                tname = self._table_lc_map.get(name.lower())
                if tname:
                    sql_signal = self._table_sql_signal_score(question, tname)
                    final = (1 - self.sql_signal_weight) * sim + self.sql_signal_weight * min(sql_signal, 2.0)
                    matched_tables.append((tname, final, sim, sql_signal))

        # 若候选实体里表太少，补充所有表做弱排序，避免 miss 主表
        if len(matched_tables) < table_budget:
            for tname in self.table_documents:
                if any(mt[0] == tname for mt in matched_tables):
                    continue
                sim = 0.0
                sql_signal = self._table_sql_signal_score(question, tname)
                final = (1 - self.sql_signal_weight) * sim + self.sql_signal_weight * min(sql_signal, 2.0)
                if final > 0:
                    matched_tables.append((tname, final, sim, sql_signal))

        matched_tables.sort(key=lambda x: x[1], reverse=True)
        seen_tables: set[str] = set()
        for tname, final_score, sim, sql_signal in matched_tables:
            if len(results) >= table_budget or tname in seen_tables:
                continue
            seen_tables.add(tname)
            meta = dict(self.table_metadata.get(tname, {}))
            meta["retrieval_source"] = "local_search_table"
            meta["semantic_score"] = round(sim, 4)
            meta["sql_signal_score"] = round(sql_signal, 4)
            node = TextNode(text=self.table_documents[tname], metadata=meta)
            results.append(NodeWithScore(node=node, score=final_score))

        # 4b) 补充上下文（社区/实体/关系/文本块），严格受 aux_budget 约束
        aux_results: list[NodeWithScore] = []

        # 社区摘要（最多 2 条）
        for cid, rank_score in ranked_communities[:2]:
            if len(aux_results) >= aux_budget:
                break
            summary = self.community_summaries.get(cid, "")
            if not summary:
                continue
            node = TextNode(
                text=f"[COMMUNITY {cid}]\n{summary}",
                metadata={
                    "doc_type": "community_summary",
                    "community_id": cid,
                    "retrieval_source": "local_search_community",
                },
            )
            aux_results.append(NodeWithScore(node=node, score=rank_score / 10000))

        # 实体描述（排除 Table）
        for name, sim in candidate_entities:
            if len(aux_results) >= aux_budget:
                break
            e = self._entity_map.get(name, {})
            if e.get("type") == "Table":
                continue
            text = (
                f"[ENTITY] {name} (类型: {e.get('type', '?')}, "
                f"度: {self._node_degree(name)})\n{e.get('description', '')}"
            )
            node = TextNode(
                text=text,
                metadata={
                    "doc_type": "entity",
                    "entity_name": name,
                    "retrieval_source": "local_search_entity",
                },
            )
            aux_results.append(NodeWithScore(node=node, score=sim))

        # 关系描述
        for r, rank_score in ranked_relations:
            if len(aux_results) >= aux_budget:
                break
            text = (
                f"[RELATION] {r['source']} -[{r['relation']}]-> {r['target']}\n"
                f"{r['description']}"
            )
            max_sim = max(
                candidate_scores.get(r["source"], 0),
                candidate_scores.get(r["target"], 0),
            )
            node = TextNode(
                text=text,
                metadata={
                    "doc_type": "relation",
                    "retrieval_source": "local_search_relation",
                },
            )
            aux_results.append(NodeWithScore(node=node, score=max_sim))

        # 文本单元
        for chunk_id, rank_score in ranked_chunks:
            if len(aux_results) >= aux_budget:
                break
            chunk = self._chunk_map.get(chunk_id)
            if not chunk:
                continue
            node = TextNode(
                text=f"[TEXT_UNIT]\n{chunk['text']}",
                metadata={
                    "doc_type": "text_unit",
                    "chunk_id": chunk_id,
                    "retrieval_source": "local_search_text_unit",
                },
            )
            aux_results.append(NodeWithScore(node=node, score=rank_score / 10000))

        results.extend(aux_results)
        # 最终安全截断
        return results[:budget]


# ---------------------------------------------------------------------------
# 工厂函数
# ---------------------------------------------------------------------------

def get_local_search_retriever(
    artifacts: dict[str, Any] | None = None,
    top_k_entities: int | None = None,
    context_budget: int | None = None,
) -> GraphRAGLocalSearch:
    """
    创建 GraphRAG 局部检索器。

    优先使用传入的 artifacts，否则从全局缓存加载。
    """
    if artifacts is None:
        from text2sql.graphrag.schema_graph import get_schema_graph_artifacts
        artifacts = get_schema_graph_artifacts()

    required = [
        "entities", "relations", "communities",
        "community_summaries", "entity_embeddings",
    ]
    missing = [k for k in required if not artifacts.get(k)]
    if missing:
        raise RuntimeError(
            f"GraphRAG 产物缺少字段: {missing}。"
            "请先运行 build_schema_graph() 构建图谱。"
        )

    return GraphRAGLocalSearch(
        artifacts=artifacts,
        top_k_entities=top_k_entities,
        context_budget=context_budget,
    )
