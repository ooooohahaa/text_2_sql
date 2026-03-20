# Text2SQL（LlamaIndex + GraphRAG）

基于 LlamaIndex 的 Text2SQL 工具，使用 GraphRAG 思想构建 **schema 与实体的关系**，在将自然语言转为 SQL 前先检索相关表，提升准确率。

## 环境

- Python ≥ 3.12
- [uv](https://github.com/astral-sh/uv) 管理虚拟环境与依赖

## 快速开始

```bash
# 进入项目目录
cd text2sql

# 使用 uv 创建虚拟环境并安装依赖（已完成则跳过）
uv sync

# 配置环境变量：复制 .env.example 为 .env 并填写
cp .env.example .env
# 编辑 .env：OPENAI_API_KEY、DATABASE_URL 等

# 运行（交互式）
uv run python main.py

# 或单次查询
uv run python main.py "查询钻石最多的前10名玩家"
```

## 项目结构

```
text2sql/
├── main.py              # 入口：构建 schema 索引 + Text2SQL 查询
├── pyproject.toml       # 依赖与项目配置（uv）
├── .env.example          # 环境变量示例
├── schema_description_for_graphrag.md   # schema 与实体描述（供 GraphRAG 解析）
├── prompts/             # 多 Agent 提示词（.txt，可多行）
├── schema/              # 建表 SQL（参考）
└── text2sql/            # 主包
    ├── config.py        # 配置（数据库 URL、OpenAI、schema 路径）
    ├── graphrag/        # GraphRAG 相关
    │   ├── schema_graph.py   # 解析 schema 文档 → 向量索引，用于“按问题检索相关表”
    │   └── __init__.py
    └── sql_engine.py    # Text2SQL 引擎（NLSQLTableQueryEngine + 可选 schema 检索）
```

## 流程简述

1. **Schema 索引**：解析 `schema_description_for_graphrag.md`，按「表 + 实体含义 + 字段」生成文档，构建 LlamaIndex `VectorStoreIndex`，用于按自然语言问题检索相关表。
2. **Text2SQL**：对每个问题先用上述检索器得到相关表，再仅在这些表上调用 `NLSQLTableQueryEngine` 生成并执行 SQL。

## 配置说明

| 变量 | 说明 |
|------|------|
| `OPENAI_API_KEY` | OpenAI API Key（必填） |
| `DATABASE_URL` | MySQL 连接串，如 `mysql+pymysql://user:pass@host:3306/DB_SPLAN_00?charset=utf8mb4` |
| `SCHEMA_DESCRIPTION_PATH` | 可选；schema 描述 Markdown 路径，默认项目根下 `schema_description_for_graphrag.md` |

## Prompt 配置

多 Agent 使用的提示词放在 **`prompts/` 目录下的 `.txt` 文件**中，可多行编辑，详见 `prompts/README.md`。  
可选：仍支持项目根目录 `prompts.json` 覆盖（JSON 字符串内用 `\n` 表示换行）；**同名 key 以 `prompts/*.txt` 为准**。

## 开发

```bash
uv sync
uv run python -c "from text2sql.graphrag import build_schema_graph; from text2sql.sql_engine import create_sql_engine; print('OK')"
```
