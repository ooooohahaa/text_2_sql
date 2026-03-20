# Prompt 文本文件

每个提示词对应一个 **UTF-8 文本文件**，文件名即逻辑名（不含扩展名）：

| 文件 | 占位符 | 说明 |
|------|--------|------|
| `llm_only_generate_sql.txt` | `{question}` `{schema_text}` | 评测模式（不连库）生成 SQL |
| `review_sql.txt` | `{question}` `{sql}` `{schema_text}` | SQL 评审 JSON 输出 |
| `llm_only_generate_sql_hint.txt` | 无（整段追加） | 可选；非空时追加在主生成模板后 |

- 可随意换行、缩进，便于阅读与版本对比。
- 除上述占位符外，正文里的 `{`、`}` 不会被替换（请避免写成 `{question}` 等同名占位符以外的误用）。

加载优先级（后者覆盖前者）：

1. 包内代码默认模板  
2. 项目根目录 `prompts.json`（可选，兼容旧配置）  
3. 本目录 `*.txt`（推荐日常维护方式）
