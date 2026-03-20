"""
Text2SQL 入口：构建 GraphRAG schema 索引并运行自然语言转 SQL 查询。

使用方式:
  uv run python main.py                    # 交互式
  uv run python main.py "查询钻石最多的前10名玩家"  # 单次查询
  uv run python main.py --build-graphrag-only     # 仅构建 GraphRAG 并退出
  uv run python main.py --query-only "问题"        # 仅查询（不构建 GraphRAG）
  uv run python main.py --testModel "问题"         # 评测：从 pgvector 加载索引后检索 schema，不连 MySQL
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
    from text2sql.runtime import (
        build_graphrag_if_needed,
        create_engine_with_optional_retriever,
        init_llm_and_embedding,
        load_schema_text,
        run_interactive,
        run_single_query,
        run_test_model,
    )

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
        help="评测模式：不连接 MySQL；从 pgvector 加载已构建索引，向量检索 schema 后生成 SQL（需先 --build-graphrag-only）",
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

    # 初始化 LLM / Embedding
    init_llm_and_embedding()

    # testModel：从 pgvector 加载索引并向量检索 schema，不连接 MySQL，只输出 SQL
    if args.testModel:
        if not args.question:
            print("testModel 模式需要传入问题，例如：python main.py --testModel \"查询钻石最多的前10名玩家\"")
            sys.exit(1)
        question = " ".join(args.question).strip()
        result = run_test_model(
            question=question,
            max_review_rounds=args.max_review_rounds,
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
    schema_path, schema_text = load_schema_text()
    retriever = build_graphrag_if_needed(query_only=args.query_only)

    if args.build_graphrag_only:
        print("已按 --build-graphrag-only 完成构建并退出。")
        return

    # 2. 创建带 schema 检索的 Text2SQL 引擎（仅生成 SQL，不执行）
    engine = create_engine_with_optional_retriever(
        query_only=args.query_only,
        retriever=retriever,
    )

    # 3. 生成 SQL（双 Agent ReAct：写作 + 评审）
    if args.question:
        question = " ".join(args.question)
        print("问题:", question)
        result = run_single_query(
            question=question,
            schema_text=schema_text,
            engine=engine,
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
    run_interactive(
        engine=engine,
        schema_text=schema_text,
        max_review_rounds=args.max_review_rounds,
        verbose_review=args.verbose_review,
    )


if __name__ == "__main__":
    main()
