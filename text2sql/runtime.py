"""
运行时封装：负责
- 初始化 LLM / Embedding
- 加载 schema 描述
- 构建 GraphRAG 并返回 retriever
- 创建 Text2SQL 引擎

CLI 层只负责解析参数和打印，高层逻辑通过这里暴露的函数来组合。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Tuple

from llama_index.core import Settings
from llama_index.embeddings.openai import OpenAIEmbedding
from llama_index.llms.openai import OpenAI

from text2sql.agents import run_multi_agent_react
from text2sql.config import (
    get_embedding_provider_config,
    get_llm_provider_config,
    get_schema_description_path,
)
from text2sql.graphrag import (
    build_schema_graph,
    get_schema_graph_artifacts,
    get_schema_retriever,
)
from text2sql.sql_engine import create_sql_engine, create_sql_engine_with_schema_retriever


def init_llm_and_embedding() -> None:
    """根据配置初始化全局 LLM 与 Embedding。"""
    llm_cfg = get_llm_provider_config()
    Settings.llm = OpenAI(
        model=llm_cfg["model"],
        api_key=llm_cfg["api_key"],
        api_base=llm_cfg["base_url"],
    )

    emb_cfg = get_embedding_provider_config()
    Settings.embed_model = OpenAIEmbedding(
        model=emb_cfg["model"],
        api_key=emb_cfg["api_key"],
        api_base=emb_cfg["base_url"],
    )


def resolve_schema_path_or_exit() -> Path:
    """
    获取 schema 描述文件路径并校验存在性。
    若不存在则打印提示并退出进程。
    """
    import sys

    schema_path = get_schema_description_path()
    if not schema_path.exists():
        print(f"未找到 schema 描述文件: {schema_path}")
        print("请将 schema_description_for_graphrag.md 放在项目根目录或设置 SCHEMA_DESCRIPTION_PATH")
        sys.exit(1)
    return schema_path


def load_schema_text() -> Tuple[Path, str]:
    """加载 schema 描述文本，返回路径与内容。"""
    schema_path = resolve_schema_path_or_exit()
    schema_text = schema_path.read_text(encoding="utf-8")
    return schema_path, schema_text


def build_graphrag_if_needed(query_only: bool) -> Any:
    """
    根据 query_only 标志决定是否构建 GraphRAG。
    - query_only=True: 跳过构建，返回 None
    - query_only=False: 构建 GraphRAG，返回 retriever
    """
    if query_only:
        print("已启用 --query-only：跳过 GraphRAG 构建。")
        return None

    print("正在构建 schema GraphRAG（图构建 + 社区摘要 + 向量索引）...")
    build_schema_graph(schema_path=get_schema_description_path())
    artifacts = get_schema_graph_artifacts()
    print(
        "GraphRAG 构建完成："
        f" nodes={len(artifacts.get('nodes', []))},"
        f" edges={len(artifacts.get('edges', []))},"
        f" communities={len(artifacts.get('communities', []))},"
        f" docs={artifacts.get('doc_count', 0)}"
    )
    return get_schema_retriever(top_k=5)


def create_engine_with_optional_retriever(query_only: bool, retriever: Any) -> Any:
    """
    创建 Text2SQL 引擎：
    - query_only=True: 直接使用全库 Text2SQL（不带 GraphRAG 检索）
    - query_only=False: 使用 schema retriever 进行相关表筛选
    """
    from text2sql.config import get_database_url

    try:
        if query_only:
            engine = create_sql_engine(sql_only=True)
        else:
            engine = create_sql_engine_with_schema_retriever(
                schema_retriever=retriever,
                sql_only=True,
            )
        return engine
    except Exception as e:  # pragma: no cover - CLI 级错误处理
        db_url = os.getenv("DATABASE_URL", "") or get_database_url()
        print("创建数据库连接失败，请检查 .env 中 DATABASE_URL:", db_url)
        print("错误详情:", e)
        import sys

        sys.exit(1)


def run_test_model(question: str, max_review_rounds: int, verbose_review: bool) -> dict:
    """
    评测模式：不构建 GraphRAG、不连接 MySQL，只输出 SQL。
    返回 run_multi_agent_react 的结果字典，供 CLI 层打印。
    """
    _, schema_text = load_schema_text()
    return run_multi_agent_react(
        question=question.strip(),
        schema_text=schema_text,
        engine=None,
        llm_only=True,
        max_rounds=max_review_rounds,
        verbose_review=verbose_review,
    )


def run_single_query(
    question: str,
    engine: Any,
    schema_text: str,
    max_review_rounds: int,
    verbose_review: bool,
) -> Dict[str, Any]:
    """
    单次查询：给定自然语言问题，使用双 Agent 生成并评审 SQL。
    仅负责调用与结果返回，打印逻辑由 CLI 层决定。
    """
    return run_multi_agent_react(
        question=question,
        schema_text=schema_text,
        engine=engine,
        llm_only=False,
        max_rounds=max_review_rounds,
        verbose_review=verbose_review,
    )


def run_interactive(
    engine: Any,
    schema_text: str,
    max_review_rounds: int,
    verbose_review: bool,
) -> None:
    """
    交互式 REPL：循环读取用户输入的问题并输出对应 SQL。
    """
    print("已就绪。当前模式：仅生成 SQL（不执行）。输入 q 退出。")
    while True:
        try:
            question = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not question or question.lower() == "q":
            break
        result = run_single_query(
            question=question,
            engine=engine,
            schema_text=schema_text,
            max_review_rounds=max_review_rounds,
            verbose_review=verbose_review,
        )
        print(f"评审结果: {'通过' if result['passed'] else '未通过'}，迭代轮次: {result['rounds']}")
        print("生成 SQL:\n", result["sql"])
        if not result["passed"]:
            last_review = result["history"][-1]["review"]
            print("最终评审问题:\n- " + "\n- ".join(last_review.get("issues", []) or ["无"]))
            print("最终改进建议:\n- " + "\n- ".join(last_review.get("suggestions", []) or ["无"]))


