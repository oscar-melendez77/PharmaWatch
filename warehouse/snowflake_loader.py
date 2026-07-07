import os

import snowflake.connector

from schema import FAERS_SCHEMA, PUBMED_SCHEMA, REDDIT_SCHEMA, DRUG_LABELS_SCHEMA


def _connect():
    return snowflake.connector.connect(
        account=os.environ["SNOWFLAKE_ACCOUNT"],
        user=os.environ["SNOWFLAKE_USER"],
        password=os.environ["SNOWFLAKE_PASSWORD"],
        database=os.environ["SNOWFLAKE_DATABASE"],
        schema=os.environ["SNOWFLAKE_SCHEMA"],
        warehouse=os.environ["SNOWFLAKE_WAREHOUSE"],
    )


def ensure_table(cursor, table_name, schema_dict):
    columns = ", ".join("{} {}".format(col, sql_type) for col, sql_type in schema_dict.items())
    ddl = "CREATE TABLE IF NOT EXISTS {} ({})".format(table_name, columns)
    cursor.execute(ddl)


def load_dataframe(df, table_name, schema_dict):
    conn = _connect()
    try:
        cursor = conn.cursor()
        try:
            ensure_table(cursor, table_name, schema_dict)
            # idempotent full refresh: clear the table before reloading so
            # re-running the pipeline for a day does not duplicate rows
            # (BigQuery loader uses WRITE_TRUNCATE for the same reason).
            cursor.execute("TRUNCATE TABLE IF EXISTS {}".format(table_name))

            columns = list(schema_dict.keys())
            placeholders = ", ".join(["%s"] * len(columns))
            col_list = ", ".join(columns)
            insert_sql = "INSERT INTO {} ({}) VALUES ({})".format(table_name, col_list, placeholders)

            rows = []
            for _, row in df.iterrows():
                rows.append(tuple(row.get(col) if col in row.index else None for col in columns))

            if rows:
                cursor.executemany(insert_sql, rows)
            conn.commit()
            full_name = "{}.{}.{}".format(
                os.environ["SNOWFLAKE_DATABASE"],
                os.environ["SNOWFLAKE_SCHEMA"],
                table_name,
            )
            print("snowflake: loaded {} rows -> {}".format(len(rows), full_name))
        finally:
            cursor.close()
    finally:
        conn.close()


def load_all(faers_df, pubmed_df, reddit_df, labels_df):
    load_dataframe(faers_df, "faers_events", FAERS_SCHEMA)
    load_dataframe(pubmed_df, "pubmed_articles", PUBMED_SCHEMA)
    load_dataframe(reddit_df, "reddit_mentions", REDDIT_SCHEMA)
    load_dataframe(labels_df, "drug_labels", DRUG_LABELS_SCHEMA)
