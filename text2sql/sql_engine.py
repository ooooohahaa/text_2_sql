"""
Text2SQL 查询引擎：基于 LlamaIndex NLSQLTableQueryEngine，
可选使用 GraphRAG schema 检索结果作为表上下文。
"""
from typing import Optional

from llama_index.core import SQLDatabase
from llama_index.core.query_engine import NLSQLTableQueryEngine
from sqlalchemy import create_engine

from text2sql.config import get_database_url


def create_sql_engine(
    database_url: Optional[str] = None,
    table_names: Optional[list[str]] = None,
    llm=None,
    sql_only: bool = True,
):
    """
    创建 Text2SQL 查询引擎。

    - database_url: 数据库连接 URL，默认从 config 读取。
    - table_names: 指定要暴露的表名列表；为 None 时使用库内所有表。
    - llm: LlamaIndex LLM 实例；为 None 时使用 Settings.llm。
    - sql_only: 为 True 时仅生成 SQL，不执行查询。
    """
    from llama_index.core import Settings

    url = database_url or get_database_url()
    engine = create_engine(url)
    sql_database = SQLDatabase(engine, include_tables=table_names)
    kwargs = {}
    if llm is not None:
        kwargs["llm"] = llm
    kwargs["sql_only"] = sql_only
    return NLSQLTableQueryEngine(sql_database=sql_database, **kwargs)


def create_sql_engine_with_schema_retriever(
    database_url: Optional[str] = None,
    schema_retriever=None,
    llm=None,
    sql_only: bool = True,
):
    """
    创建带 schema 检索的 Text2SQL 引擎：先根据自然语言问题用 GraphRAG 检索相关表，
    再仅对这些表执行 Text2SQL。schema_retriever 由 graphrag.get_schema_retriever() 提供。
    """
    from llama_index.core import Settings

    url = database_url or get_database_url()
    engine = create_engine(url)
    sql_database = SQLDatabase(engine)

    if schema_retriever is None:
        from text2sql.graphrag import get_schema_retriever
        schema_retriever = get_schema_retriever()

    class Text2SQLWithSchemaRetriever:
        """先按问题检索相关表，再仅对这些表做 Text2SQL。"""

        def __init__(self, db: SQLDatabase, retriever, llm=None):
            self._db = db
            self._retriever = retriever
            self._llm = llm or Settings.llm
            self._sql_only = sql_only

        def query(self, query_str: str):
            tables = []
            for node in self._retriever.retrieve(query_str):
                t = node.metadata.get("table") or node.metadata.get("table_name")
                if t and t not in tables:
                    tables.append(t)
            if not tables:
                qe = NLSQLTableQueryEngine(
                    sql_database=self._db,
                    llm=self._llm,
                    sql_only=self._sql_only,
                )
            else:
                db_subset = SQLDatabase(self._db.engine, include_tables=tables)
                qe = NLSQLTableQueryEngine(
                    sql_database=db_subset,
                    llm=self._llm,
                    sql_only=self._sql_only,
                )
            return qe.query(query_str)

    return Text2SQLWithSchemaRetriever(sql_database, schema_retriever, llm or Settings.llm)
