"""Single entrypoint that runs the PharmaWatch pipeline end to end.

Chains the post-ingestion stages in dependency order:

    raw Iceberg -> Spark clean -> warehouse load -> dbt marts
                -> ML train+promote -> RAG embed

Ingestion to S3 + raw Iceberg is handled by the Airflow DAGs (faers/pubmed/
openfda) or the master DAG; by default this runner starts at ingest_raw so it
also works standalone once raw JSON is in S3. Each stage shells out to the
same tool it would use in production (spark-submit, sbt, dbt, python), so this
is a portable orchestrator for local runs and CI — no Airflow scheduler needed.

Examples:
    python orchestration/pipeline.py                 # full run, ds=today (UTC)
    python orchestration/pipeline.py --ds 2024-01-01
    python orchestration/pipeline.py --from warehouse_load
    python orchestration/pipeline.py --only dbt_build ml_train
    python orchestration/pipeline.py --skip rag_embed --list
"""
import argparse
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

ROOT = os.environ.get(
    "PHARMAWATCH_ROOT",
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)

ICEBERG_PACKAGES = (
    "org.apache.iceberg:iceberg-spark-runtime-3.5_2.12:1.5.2,"
    "software.amazon.awssdk:bundle:2.24.8,"
    "software.amazon.awssdk:url-connection-client:2.24.8"
)


def _p(*parts):
    return os.path.join(ROOT, *parts)


def _stages(ds):
    """Ordered (name, command, cwd) tuples. cwd=None means run from ROOT."""
    dbt_dir = _p("dbt", "pharmawatch")
    return [
        (
            "ingest_raw",
            ["spark-submit", "--packages", ICEBERG_PACKAGES, _p("iceberg", "ingest_raw.py"), "all", ds],
            None,
        ),
        (
            "transform",
            ["sbt", "runMain pharmawatch.TransformerRunner"],
            _p("spark"),
        ),
        (
            "warehouse_load",
            ["spark-submit", "--packages", ICEBERG_PACKAGES, _p("warehouse", "run_load.py")],
            None,
        ),
        (
            "dbt_build",
            ["dbt", "build", "--project-dir", dbt_dir, "--profiles-dir", dbt_dir],
            None,
        ),
        (
            "ml_train",
            [sys.executable, _p("ml", "train.py")],
            None,
        ),
        (
            "rag_embed",
            [sys.executable, _p("rag", "embed.py")],
            None,
        ),
    ]


def _select(all_stages, args):
    names = [s[0] for s in all_stages]
    if args.only:
        chosen = set(args.only)
        unknown = chosen - set(names)
        if unknown:
            raise SystemExit("unknown stage(s): {}".format(", ".join(sorted(unknown))))
        selected = [s for s in all_stages if s[0] in chosen]
    else:
        selected = list(all_stages)
        if args.from_stage:
            if args.from_stage not in names:
                raise SystemExit("unknown --from stage: {}".format(args.from_stage))
            start = names.index(args.from_stage)
            selected = all_stages[start:]
    if args.skip:
        selected = [s for s in selected if s[0] not in set(args.skip)]
    return selected


def run(args):
    ds = args.ds or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    all_stages = _stages(ds)
    selected = _select(all_stages, args)

    if args.list:
        print("stages (ds={}):".format(ds))
        for name, cmd, cwd in all_stages:
            mark = "run " if name in {s[0] for s in selected} else "skip"
            print("  [{}] {:<15} {}".format(mark, name, " ".join(cmd)))
        return 0

    print("PharmaWatch pipeline — ds={} — {} stage(s)".format(ds, len(selected)))
    results = []
    for name, cmd, cwd in selected:
        print("\n=== {} ===".format(name))
        print("  $ {}".format(" ".join(cmd)))
        if args.dry_run:
            results.append((name, "dry-run", 0.0))
            continue
        started = time.time()
        proc = subprocess.run(cmd, cwd=cwd)
        elapsed = time.time() - started
        if proc.returncode != 0:
            results.append((name, "FAILED", elapsed))
            _summary(results)
            print("\npipeline halted: {} exited {}".format(name, proc.returncode))
            return proc.returncode
        results.append((name, "ok", elapsed))

    _summary(results)
    print("\npipeline complete")
    return 0


def _summary(results):
    print("\n--- summary ---")
    for name, status, elapsed in results:
        print("  {:<15} {:<8} {:.1f}s".format(name, status, elapsed))


def main():
    parser = argparse.ArgumentParser(description="Run the PharmaWatch pipeline.")
    parser.add_argument("--ds", help="run date YYYY-MM-DD (default: today UTC)")
    parser.add_argument("--from", dest="from_stage", help="start at this stage")
    parser.add_argument("--only", nargs="+", help="run only these stages")
    parser.add_argument("--skip", nargs="+", help="skip these stages")
    parser.add_argument("--list", action="store_true", help="show stages and exit")
    parser.add_argument("--dry-run", action="store_true", help="print commands without running")
    return run(parser.parse_args())


if __name__ == "__main__":
    sys.exit(main())
