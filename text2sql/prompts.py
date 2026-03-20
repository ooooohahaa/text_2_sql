from __future__ import annotations

"""
Prompt 管理：
- 优先从项目根目录的 prompts.json 读取
- 若缺失或字段缺失，则回退到内置默认模板

prompts.json 示例结构（示意）：
{
  "llm_only_generate_sql": "... {question} ... {schema_text} ...",
  "review_sql": "... {question} ... {sql} ... {schema_text} ...",

  // 可选：仅当存在该字段时，才会被自动注入到 llm_only_generate_sql 提示中
  // 示例用法：few-shot / CoT 额外说明、示例等
  "llm_only_generate_sql_hint": "这里写 few-shot / CoT 风格的额外提示词，将自动追加在主提示词之后。"
}
"""

import json
from pathlib import Path
from typing import Dict


_DEFAULT_PROMPTS: Dict[str, str] = {
    "llm_only_generate_sql": """你是一个资深 MySQL SQL 生成助手。请根据给定 schema 描述，为用户问题生成 SQL。

要求：
1) 只输出 SQL，不要解释。
2) 使用 MySQL 5.7 兼容语法。
3) 若问题信息不足，尽量给出最合理的可执行 SQL（可带保守条件）。
4) 不要执行 SQL。

【用户问题】
{question}

【Schema 描述】
{schema_text}
""",
    "review_sql": """你是资深 SQL 评审专家。请评审候选 SQL 是否能正确回答用户问题，并符合给定 schema。

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
""",
}

_PROMPTS_CACHE: Dict[str, str] | None = None


def _load_prompts_from_file() -> Dict[str, str]:
    """
    从项目根目录的 prompts.json 读取配置。
    - 若文件不存在或解析失败，则返回空 dict，由默认模板兜底。
    """
    project_root = Path(__file__).resolve().parents[1]
    config_path = project_root / "prompts.json"
    if not config_path.exists():
        return {}
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            # 仅接受 str->str 的键值
            return {str(k): str(v) for k, v in data.items()}
    except Exception:
        return {}
    return {}


def _ensure_prompts_loaded() -> None:
    global _PROMPTS_CACHE
    if _PROMPTS_CACHE is not None:
        return
    file_prompts = _load_prompts_from_file()
    merged = dict(_DEFAULT_PROMPTS)
    merged.update(file_prompts)
    _PROMPTS_CACHE = merged


def get_prompt(name: str) -> str:
    """
    获取指定名称的 prompt 模板。
    优先使用 prompts.json 中的配置，缺失时回退到内置默认模板。
    """
    _ensure_prompts_loaded()
    assert _PROMPTS_CACHE is not None
    return _PROMPTS_CACHE.get(name, _DEFAULT_PROMPTS.get(name, ""))

