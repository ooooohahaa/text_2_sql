from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from llama_index.core import Settings

from text2sql.prompts import get_prompt


def _extract_sql(resp: Any) -> str:
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


def _extract_table_column_whitelist(schema_text: str) -> dict[str, set[str]]:
    """
    从 schema 上下文中抽取白名单: {table_name -> set(columns)}。
    兼容 [TABLE] 片段格式。
    """
    whitelist: dict[str, set[str]] = {}
    lines = (schema_text or "").splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line == "[TABLE]":
            table_name = ""
            cols: set[str] = set()
            j = i + 1
            while j < len(lines):
                s = lines[j].strip()
                if s.startswith("[TABLE]") or s.startswith("[COMMUNITY") or s.startswith("### "):
                    break
                if s.startswith("表名:"):
                    table_name = s.split(":", 1)[1].strip()
                m = re.match(r"^-\s*([a-zA-Z_]\w*)\s*\(", s)
                if m:
                    cols.add(m.group(1))
                j += 1
            if table_name:
                whitelist[table_name] = cols
            i = j
            continue
        i += 1
    return whitelist


def _deterministic_sql_check(sql: str, schema_text: str) -> Dict[str, Any]:
    """
    Deterministic checker:
    1) 用 sqlglot 检查 MySQL 语法
    2) 提取 table/column，与 schema 白名单比对
    """
    issues: list[str] = []
    suggestions: list[str] = []
    sql = (sql or "").strip()
    if not sql:
        return {
            "pass": False,
            "issues": ["SQL 为空。"],
            "suggestions": ["请输出一条完整可执行的 MySQL SQL。"],
        }

    try:
        import sqlglot
        from sqlglot import exp
    except Exception:
        return {"pass": True, "issues": [], "suggestions": []}

    try:
        parsed = sqlglot.parse_one(sql, read="mysql")
    except Exception as exc:
        return {
            "pass": False,
            "issues": [f"MySQL 语法解析失败: {exc}"],
            "suggestions": ["请使用 MySQL 5.7 兼容语法重写 SQL。"],
        }

    whitelist = _extract_table_column_whitelist(schema_text)
    if not whitelist:
        return {"pass": True, "issues": [], "suggestions": []}

    def _fields_hint(table_name: str) -> str:
        cols = sorted(whitelist.get(table_name, set()))
        if not cols:
            return f"表 `{table_name}` 未提取到字段列表。"
        # 避免提示过长影响上下文
        shown = cols[:80]
        suffix = " ..." if len(cols) > 80 else ""
        return f"表 `{table_name}` 可用字段: {', '.join(shown)}{suffix}"

    table_alias_to_name: dict[str, str] = {}
    referenced_tables: set[str] = set()
    emitted_field_hints: set[str] = set()

    for t in parsed.find_all(exp.Table):
        raw_name = t.name
        alias = t.alias_or_name
        if not raw_name:
            continue
        matched = None
        for real_t in whitelist:
            if real_t.lower() == raw_name.lower():
                matched = real_t
                break
        if matched is None:
            issues.append(f"引用了不在检索 schema 白名单中的表: {raw_name}")
            suggestions.append("请仅使用检索到的表名，或先补充更相关的 schema 上下文。")
            continue
        referenced_tables.add(matched)
        table_alias_to_name[alias] = matched
        table_alias_to_name[raw_name] = matched

    for c in parsed.find_all(exp.Column):
        col = c.name
        tbl = c.table
        if not col:
            continue
        if tbl:
            resolved = table_alias_to_name.get(tbl)
            if not resolved:
                issues.append(f"字段引用了未知表/别名: {tbl}.{col}")
                suggestions.append("请检查 JOIN 中的表别名与字段前缀是否一致。")
                continue
            if col not in whitelist.get(resolved, set()):
                issues.append(f"字段不存在: {resolved}.{col}")
                suggestions.append(f"请改用表 `{resolved}` 中实际存在的字段名。")
                if resolved not in emitted_field_hints:
                    suggestions.append(_fields_hint(resolved))
                    emitted_field_hints.add(resolved)
        else:
            # 未带表前缀的字段
            if len(referenced_tables) == 1:
                only_t = next(iter(referenced_tables))
                if col not in whitelist.get(only_t, set()):
                    issues.append(f"字段不存在: {only_t}.{col}")
                    suggestions.append("请确认字段名拼写，或补充正确字段。")
                    if only_t not in emitted_field_hints:
                        suggestions.append(_fields_hint(only_t))
                        emitted_field_hints.add(only_t)
            elif len(referenced_tables) > 1:
                exists = any(col in whitelist.get(t, set()) for t in referenced_tables)
                if not exists:
                    issues.append(f"未限定字段且在候选表中不存在: {col}")
                    suggestions.append("多表查询时请使用 `表别名.字段名`，并确保字段真实存在。")
                    for t in sorted(referenced_tables):
                        if t not in emitted_field_hints:
                            suggestions.append(_fields_hint(t))
                            emitted_field_hints.add(t)

    return {
        "pass": not issues,
        "issues": issues,
        "suggestions": suggestions,
    }


def _llm_only_generate_sql(question: str, schema_text: str) -> str:
    """
    评测模式：不依赖数据库连接，仅基于 schema 文本生成 SQL。
    """
    template = get_prompt("llm_only_generate_sql")

    # 从 prompts/llm_only_generate_sql_hint.txt（或 prompts.json）读取可选 hint
    # - 若未配置或为空，get_prompt 返回空串，不追加
    # - 若存在，则会作为补充提示追加在主模板之后（适合 few-shot / CoT 说明）
    hint = get_prompt("llm_only_generate_sql_hint")
    if hint and hint.strip():
        template = f"{template}\n\n{hint.strip()}"

    # 为避免 JSON 中花括号与 format 语法冲突，这里使用简单占位符替换而非 str.format
    prompt = (
        template.replace("{question}", str(question))
        .replace("{schema_text}", str(schema_text))
    )
    print("--------------------------------")
    print(prompt)
    print("--------------------------------")
    resp = Settings.llm.complete(prompt)
    text = str(getattr(resp, "text", "") or resp)
    return _clean_sql_text(text)


def _review_sql(question: str, sql: str, schema_text: str) -> Dict[str, Any]:
    """
    评审 Agent：检查 SQL 是否存在语义/语法/约束问题，返回结构化结果。
    返回格式:
    {
        "pass": bool,
        "issues": [str, ...],
        "suggestions": [str, ...]
    }
    """
    # 先做 deterministic checker，失败则直接返回结构化结果
    deterministic = _deterministic_sql_check(sql=sql, schema_text=schema_text)
    if not deterministic.get("pass"):
        return deterministic

    template = get_prompt("review_sql")
    review_prompt = (
        template.replace("{question}", str(question))
        .replace("{sql}", str(sql))
        .replace("{schema_text}", str(schema_text))
    )
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
    engine: Any = None,
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


def run_multi_agent_react(
    question: str,
    schema_text: str,
    engine: Any = None,
    llm_only: bool = False,
    max_rounds: int = 3,
    verbose_review: bool = False,
) -> Dict[str, Any]:
    """
    双 Agent ReAct 迭代：
    写作Agent生成 SQL -> 评审Agent评审 -> 通过则结束，否则带意见打回继续。
    """
    max_rounds = max(1, int(max_rounds))
    review_feedback = ""
    history: List[Dict[str, Any]] = []

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

