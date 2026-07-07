"""Master DAG: full PharmaWatch pipeline on a daily schedule.

Triggers the three ingestion DAGs (each lands raw data in Iceberg via its own
ingest_to_iceberg task), then runs the downstream chain:

    ingest (faers/pubmed/openfda) -> Spark clean -> warehouse load
        -> dbt build -> ml train+promote -> rag embed

The heavy steps shell out to the same commands orchestration/pipeline.py uses,
so behaviour matches a standalone run. $PHARMAWATCH_ROOT must be set on the
Airflow workers.
"""
from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.operators.trigger_dagrun import TriggerDagRunOperator

ICEBERG_PACKAGES = (
    "org.apache.iceberg:iceberg-spark-runtime-3.5_2.12:1.5.2,"
    "software.amazon.awssdk:bundle:2.24.8,"
    "software.amazon.awssdk:url-connection-client:2.24.8"
)

ROOT = "$PHARMAWATCH_ROOT"

default_args = {
    "owner": "pharmawatch",
    "retries": 1,
    "retry_delay": timedelta(minutes=5),
}


def _trigger(source):
    return TriggerDagRunOperator(
        task_id="trigger_{}".format(source),
        trigger_dag_id="{}_daily".format(source),
        wait_for_completion=True,
        poke_interval=60,
        reset_dag_run=True,
        allowed_states=["success"],
        failed_states=["failed"],
    )


with DAG(
    dag_id="master_pipeline",
    default_args=default_args,
    start_date=datetime(2024, 1, 1),
    schedule_interval="0 9 * * *",  # after the 06/07/08 UTC ingestion DAGs
    catchup=False,
    max_active_runs=1,
) as dag:
    trigger_faers = _trigger("faers")
    trigger_pubmed = _trigger("pubmed")
    trigger_openfda = _trigger("openfda")

    transform = BashOperator(
        task_id="spark_transform",
        bash_command='cd {}/spark && sbt "runMain pharmawatch.TransformerRunner"'.format(ROOT),
    )

    warehouse_load = BashOperator(
        task_id="warehouse_load",
        bash_command="spark-submit --packages {pkgs} {root}/warehouse/run_load.py".format(
            pkgs=ICEBERG_PACKAGES, root=ROOT
        ),
    )

    dbt_build = BashOperator(
        task_id="dbt_build",
        bash_command="dbt build --project-dir {root}/dbt/pharmawatch --profiles-dir {root}/dbt/pharmawatch".format(
            root=ROOT
        ),
    )

    ml_train = BashOperator(
        task_id="ml_train",
        bash_command="python {root}/ml/train.py".format(root=ROOT),
    )

    rag_embed = BashOperator(
        task_id="rag_embed",
        bash_command="python {root}/rag/embed.py".format(root=ROOT),
    )

    [trigger_faers, trigger_pubmed, trigger_openfda] >> transform
    transform >> warehouse_load >> dbt_build >> ml_train >> rag_embed
