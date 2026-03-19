"""
Text2SQL 入口：构建 GraphRAG schema 索引并运行自然语言转 SQL 查询。

使用方式:
  uv run python main.py                    # 交互式
  uv run python main.py "查询钻石最多的前10名玩家"  # 单次查询
  uv run python main.py --build-graphrag-only     # 仅构建 GraphRAG 并退出
  uv run python main.py --query-only "问题"        # 仅查询（不构建 GraphRAG）
  uv run python main.py --testModel "问题"         # 评测模式：不连 MySQL，仅输出 SQL
"""
import argparse
import json
import os
import sys

# 确保项目根在 path 中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _ensure_env():
    """确保必要的环境变量已设置（可从 .env 加载）。"""
    from dotenv import load_dotenv
    from pathlib import Path
    load_dotenv(Path(__file__).parent / ".env")


def main():
    _ensure_env()
    from llama_index.core import Settings
    from llama_index.embeddings.openai import OpenAIEmbedding
    from llama_index.llms.openai import OpenAI

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

    # 默认评审轮次优先从环境变量读取，可被命令行参数覆盖
    default_review_rounds = int(os.getenv("REVIEW_MAX_ROUNDS", "3"))

    parser = argparse.ArgumentParser(description="Text2SQL + GraphRAG 入口")
    parser.add_argument(
        "--build-graphrag-only",
        action="store_true",
        help="仅执行 GraphRAG 构建，不进入 Text2SQL 查询流程",
    )
    parser.add_argument(
        "--query-only",
        action="store_true",
        help="仅执行查询流程，不构建 GraphRAG（直接使用全库 Text2SQL）",
    )
    parser.add_argument(
        "--testModel",
        action="store_true",
        help="评测模式：不连接 MySQL、不执行查询，仅调用 LLM 生成 SQL",
    )
    parser.add_argument(
        "question",
        nargs="*",
        help="自然语言问题；不传则进入交互式",
    )
    parser.add_argument(
        "--max-review-rounds",
        type=int,
        default=default_review_rounds,
        help="写作/评审最大迭代轮次（默认 3，或由环境变量 REVIEW_MAX_ROUNDS 覆盖）",
    )
    parser.add_argument(
        "--verbose-review",
        action="store_true",
        help="输出每轮评审详细日志（中间 SQL、问题、建议）",
    )
    args = parser.parse_args()

    # 设置 LLM（从 .env 读取，支持第三方 OpenAI 兼容供应商）
    llm_cfg = get_llm_provider_config()
    Settings.llm = OpenAI(
        model=llm_cfg["model"],
        api_key=llm_cfg["api_key"],
        api_base=llm_cfg["base_url"],
    )
    # 设置 Embedding（与 LLM 分离配置）
    emb_cfg = get_embedding_provider_config()
    Settings.embed_model = OpenAIEmbedding(
        model=emb_cfg["model"],
        api_key=emb_cfg["api_key"],
        api_base=emb_cfg["base_url"],
    )

    def _extract_sql(resp) -> str:
        """兼容不同返回结构，尽量提取生成的 SQL。"""
        if hasattr(resp, "metadata") and isinstance(resp.metadata, dict):
            sql = resp.metadata.get("sql_query")
            if sql:
                return str(sql)
        if hasattr(resp, "response") and resp.response:
            return str(resp.response)
        return str(resp)

    def _clean_sql_text(text: str) -> str:
        """清理 SQL 文本，去掉 markdown 包裹。"""
        text = (text or "").strip()
        if "```" in text:
            parts = [p.strip() for p in text.split("```") if p.strip()]
            for p in parts:
                if p.lower().startswith("sql"):
                    return p[3:].strip()
            return parts[0]
        return text

    def _llm_only_generate_sql(question: str, schema_text: str) -> str:
        """
        评测模式：不依赖数据库连接，仅基于 schema 文本生成 SQL。
        """
        prompt = f"""你是一个资深 MySQL SQL 生成助手。请根据给定 schema 描述，为用户问题生成 SQL。

要求：
1) 只输出 SQL，不要解释。
2) 使用 MySQL 5.7 兼容语法。
3) 若问题信息不足，尽量给出最合理的可执行 SQL（可带保守条件）。
4) 不要执行 SQL。

【用户问题】
{question}

【Schema 描述】
{schema_text}
"""
        resp = Settings.llm.complete(prompt)
        text = str(getattr(resp, "text", "") or resp)
        return _clean_sql_text(text)

    def _review_sql(question: str, sql: str, schema_text: str) -> dict:
        """
        评审 Agent：检查 SQL 是否存在语义/语法/约束问题，返回结构化结果。
        返回格式:
        {
            "pass": bool,
            "issues": [str, ...],
            "suggestions": [str, ...]
        }
        """
        review_prompt = f"""你是资深 SQL 评审专家。请评审候选 SQL 是否能正确回答用户问题，并符合给定 schema。

请严格按 JSON 输出，且只输出 JSON，不要任何解释。格式如下：
{{
  "pass": true/false,
  "issues": ["问题1", "问题2"],
  "suggestions": ["改进建议1", "改进建议2"]
}}

评审标准：
1) 是否与用户问题语义一致；
2) 表/字段是否来自 schema 且关联合理；
3) MySQL 5.7 语法是否可执行；
4) 是否遗漏关键过滤、分组、排序、聚合逻辑；
5) 若问题本身含糊，允许合理假设，但要在 issues/suggestions 指出。

【用户问题】
{question}

【候选 SQL】
{sql}

【Schema 描述】
{schema_text}
"""
        raw = str(getattr(Settings.llm.complete(review_prompt), "text", "") or "")
        raw = raw.strip()
        if "```" in raw:
            raw = _clean_sql_text(raw)
        try:
            parsed = json.loads(raw)
            return {
                "pass": bool(parsed.get("pass", False)),
                "issues": [str(i) for i in parsed.get("issues", []) if str(i).strip()],
                "suggestions": [str(i) for i in parsed.get("suggestions", []) if str(i).strip()],
            }
        except Exception:
            # 容错：若模型未按 JSON 输出，默认不通过并回传原文
            return {
                "pass": False,
                "issues": ["评审输出非 JSON，无法可靠解析。"],
                "suggestions": [raw[:500] if raw else "请按 JSON 格式返回评审意见。"],
            }

    def _writer_agent_generate_sql(
        question: str,
        schema_text: str,
        engine=None,
        review_feedback: str = "",
        llm_only: bool = False,
    ) -> str:
        """
        写作 Agent：根据问题（和上一轮评审反馈）产出 SQL。
        - llm_only=True: 不连库，只基于 schema 文本生成（testModel）
        - llm_only=False: 使用 Text2SQL 引擎生成
        """
        if llm_only:
            enriched_question = question
            if review_feedback.strip():
                enriched_question = (
                    f"{question}\n\n"
                    f"请根据以下评审意见修正 SQL，并仅输出最终 SQL：\n{review_feedback}"
                )
            return _llm_only_generate_sql(enriched_question, schema_text)

        enriched_question = question
        if review_feedback.strip():
            enriched_question = (
                f"{question}\n\n"
                f"修正要求（来自 SQL 评审）：\n{review_feedback}\n"
                f"请基于修正要求重写 SQL。"
            )
        resp = engine.query(enriched_question)
        return _clean_sql_text(_extract_sql(resp))

    def _run_multi_agent_react(
        question: str,
        schema_text: str,
        engine=None,
        llm_only: bool = False,
        max_rounds: int = 3,
        verbose_review: bool = False,
    ) -> dict:
        """
        双 Agent ReAct 迭代：
        写作Agent生成 SQL -> 评审Agent评审 -> 通过则结束，否则带意见打回继续。
        """
        max_rounds = max(1, int(max_rounds))
        review_feedback = ""
        history = []

        for round_idx in range(1, max_rounds + 1):
            sql = _writer_agent_generate_sql(
                question=question,
                schema_text=schema_text,
                engine=engine,
                review_feedback=review_feedback,
                llm_only=llm_only,
            )
            review = _review_sql(question=question, sql=sql, schema_text=schema_text)
            history.append({"round": round_idx, "sql": sql, "review": review})

            if verbose_review:
                print(f"\n--- 评审轮次 {round_idx}/{max_rounds} ---")
                print("写作Agent中间 SQL:\n", sql)
                print("评审Agent结论:", "通过" if review.get("pass") else "未通过")
                print("评审问题:\n- " + "\n- ".join(review.get("issues", []) or ["无"]))
                print("改进建议:\n- " + "\n- ".join(review.get("suggestions", []) or ["无"]))

            if review.get("pass"):
                return {
                    "passed": True,
                    "sql": sql,
                    "rounds": round_idx,
                    "history": history,
                }

            issues = review.get("issues", [])
            suggestions = review.get("suggestions", [])
            review_feedback = "问题:\n- " + "\n- ".join(issues or ["无"]) + "\n改进建议:\n- " + "\n- ".join(
                suggestions or ["请修复上述问题。"]
            )

        # 达到最大轮次仍未通过，返回最后一版 SQL 和评审记录
        last = history[-1]
        return {
            "passed": False,
            "sql": last["sql"],
            "rounds": max_rounds,
            "history": history,
        }

    # testModel：不构建 GraphRAG，不连接 MySQL，只输出 SQL
    if args.testModel:
        if not args.question:
            print("testModel 模式需要传入问题，例如：python main.py --testModel \"查询钻石最多的前10名玩家\"")
            sys.exit(1)
        question = " ".join(args.question).strip()
        schema_path = get_schema_description_path()
        if not schema_path.exists():
            print(f"未找到 schema 描述文件: {schema_path}")
            sys.exit(1)
        schema_text = schema_path.read_text(encoding="utf-8")
        result = _run_multi_agent_react(
            question=question,
            schema_text=schema_text,
            engine=None,
            llm_only=True,
            max_rounds=args.max_review_rounds,
            verbose_review=args.verbose_review,
        )
        print(f"评审结果: {'通过' if result['passed'] else '未通过'}，迭代轮次: {result['rounds']}")
        print("生成 SQL:\n", result["sql"])
        if not result["passed"]:
            last_review = result["history"][-1]["review"]
            print("最终评审问题:\n- " + "\n- ".join(last_review.get("issues", []) or ["无"]))
            print("最终改进建议:\n- " + "\n- ".join(last_review.get("suggestions", []) or ["无"]))
        return

    # 1. 构建 schema 向量索引（用于按问题检索相关表）
    schema_path = get_schema_description_path()
    if not schema_path.exists():
        print(f"未找到 schema 描述文件: {schema_path}")
        print("请将 schema_description_for_graphrag.md 放在项目根目录或设置 SCHEMA_DESCRIPTION_PATH")
        sys.exit(1)

    retriever = None
    if not args.query_only:
        print("正在构建 schema GraphRAG（图构建 + 社区摘要 + 向量索引）...")
        build_schema_graph(schema_path=schema_path)
        artifacts = get_schema_graph_artifacts()
        print(
            "GraphRAG 构建完成："
            f" nodes={len(artifacts.get('nodes', []))},"
            f" edges={len(artifacts.get('edges', []))},"
            f" communities={len(artifacts.get('communities', []))},"
            f" docs={artifacts.get('doc_count', 0)}"
        )
        retriever = get_schema_retriever(top_k=5)
    else:
        print("已启用 --query-only：跳过 GraphRAG 构建。")

    if args.build_graphrag_only:
        print("已按 --build-graphrag-only 完成构建并退出。")
        return

    # 2. 创建带 schema 检索的 Text2SQL 引擎（仅生成 SQL，不执行）
    try:
        if args.query_only:
            engine = create_sql_engine(sql_only=True)
        else:
            engine = create_sql_engine_with_schema_retriever(
                schema_retriever=retriever,
                sql_only=True,
            )
    except Exception as e:
        print("创建数据库连接失败，请检查 .env 中 DATABASE_URL:", e)
        sys.exit(1)

    # 3. 生成 SQL（双 Agent ReAct：写作 + 评审）
    schema_text = schema_path.read_text(encoding="utf-8")
    if args.question:
        question = " ".join(args.question)
        print("问题:", question)
        result = _run_multi_agent_react(
            question=question,
            schema_text=schema_text,
            engine=engine,
            llm_only=False,
            max_rounds=args.max_review_rounds,
            verbose_review=args.verbose_review,
        )
        print(f"评审结果: {'通过' if result['passed'] else '未通过'}，迭代轮次: {result['rounds']}")
        print("生成 SQL:\n", result["sql"])
        if not result["passed"]:
            last_review = result["history"][-1]["review"]
            print("最终评审问题:\n- " + "\n- ".join(last_review.get("issues", []) or ["无"]))
            print("最终改进建议:\n- " + "\n- ".join(last_review.get("suggestions", []) or ["无"]))
        return

    # 交互式
    print("已就绪。当前模式：仅生成 SQL（不执行）。输入 q 退出。")
    while True:
        try:
            question = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not question or question.lower() == "q":
            break
        result = _run_multi_agent_react(
            question=question,
            schema_text=schema_text,
            engine=engine,
            llm_only=False,
            max_rounds=args.max_review_rounds,
            verbose_review=args.verbose_review,
        )
        print(f"评审结果: {'通过' if result['passed'] else '未通过'}，迭代轮次: {result['rounds']}")
        print("生成 SQL:\n", result["sql"])
        if not result["passed"]:
            last_review = result["history"][-1]["review"]
            print("最终评审问题:\n- " + "\n- ".join(last_review.get("issues", []) or ["无"]))
            print("最终改进建议:\n- " + "\n- ".join(last_review.get("suggestions", []) or ["无"]))


if __name__ == "__main__":
    main()
