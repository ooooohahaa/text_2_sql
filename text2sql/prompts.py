from __future__ import annotations

"""
Prompt 管理（推荐：项目根目录 prompts/*.txt，可多行、易读）

加载顺序（后者覆盖前者）：
1. 内置默认模板 _DEFAULT_PROMPTS
2. 项目根目录 prompts.json（可选，兼容旧版单行 JSON 字符串）
3. 项目根目录 prompts/<name>.txt（推荐日常维护）

占位符在 agents 中用简单字符串替换注入：
- llm_only_generate_sql: {question}, {schema_text}
- review_sql: {question}, {sql}, {schema_text}
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
    # 可选；默认空，由 prompts/llm_only_generate_sql_hint.txt 覆盖
    "llm_only_generate_sql_hint": "",
}

_PROMPTS_CACHE: Dict[str, str] | None = None


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_prompts_from_json() -> Dict[str, str]:
    """从 prompts.json 读取（可选）。解析失败返回空 dict。"""
    config_path = _project_root() / "prompts.json"
    if not config_path.exists():
        return {}
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items() if isinstance(v, str)}
    except Exception:
        return {}
    return {}


def _load_prompts_from_txt_dir() -> Dict[str, str]:
    """
    从 prompts/*.txt 读取：文件名（不含 .txt）为 key，文件内容为模板。
    """
    prompts_dir = _project_root() / "prompts"
    if not prompts_dir.is_dir():
        return {}
    out: Dict[str, str] = {}
    for path in sorted(prompts_dir.glob("*.txt")):
        key = path.stem
        if not key:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        out[key] = text
    return out


def _ensure_prompts_loaded() -> None:
    global _PROMPTS_CACHE
    if _PROMPTS_CACHE is not None:
        return
    merged = dict(_DEFAULT_PROMPTS)
    merged.update(_load_prompts_from_json())
    merged.update(_load_prompts_from_txt_dir())
    _PROMPTS_CACHE = merged


def get_prompt(name: str) -> str:
    """
    获取指定名称的 prompt 模板。
    txt 目录中的配置优先于 prompts.json，二者均覆盖内置默认。
    """
    _ensure_prompts_loaded()
    assert _PROMPTS_CACHE is not None
    return _PROMPTS_CACHE.get(name, _DEFAULT_PROMPTS.get(name, ""))


def reload_prompts() -> None:
    """测试或热加载时可调用，清空缓存后下次 get_prompt 重新读盘。"""
    global _PROMPTS_CACHE
    _PROMPTS_CACHE = None
