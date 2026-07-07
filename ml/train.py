import os

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import mlflow
import mlflow.lightgbm
from mlflow.tracking import MlflowClient
import shap
from lightgbm import LGBMClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import roc_auc_score, f1_score, log_loss

from features import build_features, CATEGORICAL_COLUMNS

MODEL_PARAMS = {
    "num_leaves": 31,
    "learning_rate": 0.05,
    "n_estimators": 200,
    "min_child_samples": 20,
    "objective": "binary",
    "metric": "auc",
}

TARGETS = {
    "serious": "y_serious",
    "hospitalization": "y_hospitalization",
    "death": "y_death",
    "disability": "y_disability",
}

EXPERIMENT_NAME = "pharmawatch-risk-model"

# a model must clear this AUC to be promotable at all, and must also beat the
# current Production model's AUC (champion/challenger) to replace it.
MIN_PRODUCTION_AUC = 0.55


def load_master_from_warehouse():
    warehouse = os.environ.get("WAREHOUSE", "").lower()
    if warehouse == "snowflake":
        import snowflake.connector
        conn = snowflake.connector.connect(
            account=os.environ["SNOWFLAKE_ACCOUNT"],
            user=os.environ["SNOWFLAKE_USER"],
            password=os.environ["SNOWFLAKE_PASSWORD"],
            database=os.environ["SNOWFLAKE_DATABASE"],
            schema=os.environ["SNOWFLAKE_SCHEMA"],
            warehouse=os.environ["SNOWFLAKE_WAREHOUSE"],
        )
        try:
            cursor = conn.cursor()
            try:
                cursor.execute("SELECT * FROM drug_master_profile")
                rows = cursor.fetchall()
                columns = [d[0].lower() for d in cursor.description]
                df = pd.DataFrame(rows, columns=columns)
            finally:
                cursor.close()
        finally:
            conn.close()
        return df
    if warehouse == "bigquery":
        from google.cloud import bigquery
        client = bigquery.Client(project=os.environ["GCP_PROJECT_ID"])
        table = "{}.{}.drug_master_profile".format(
            os.environ["GCP_PROJECT_ID"], os.environ["BIGQUERY_DATASET"]
        )
        df = client.query("SELECT * FROM `{}`".format(table)).to_dataframe()
        df.columns = [c.lower() for c in df.columns]
        return df
    raise ValueError("set WAREHOUSE to 'snowflake' or 'bigquery'")


def binarize(y_float):
    return (np.asarray(y_float) > 0).astype(int)


def train_one_model(X, y_binary):
    X_train, X_test, y_train, y_test = train_test_split(
        X, y_binary, test_size=0.2, random_state=42
    )
    model = LGBMClassifier(**MODEL_PARAMS)
    model.fit(X_train, y_train, categorical_feature=CATEGORICAL_COLUMNS)

    y_pred_proba = model.predict_proba(X_test)[:, 1]
    y_pred = (y_pred_proba >= 0.5).astype(int)

    metrics = {}
    try:
        metrics["auc"] = float(roc_auc_score(y_test, y_pred_proba))
    except ValueError:
        metrics["auc"] = 0.0
    metrics["f1"] = float(f1_score(y_test, y_pred, zero_division=0))
    try:
        metrics["log_loss"] = float(log_loss(y_test, y_pred_proba, labels=[0, 1]))
    except ValueError:
        metrics["log_loss"] = 0.0

    return model, metrics, X_test


def _production_auc(client, name):
    """AUC of the model currently in Production, or None if there isn't one."""
    prod = client.get_latest_versions(name, stages=["Production"])
    if not prod:
        return None
    tag = prod[0].tags.get("auc")
    try:
        return float(tag) if tag is not None else None
    except (TypeError, ValueError):
        return None


def promote_to_production(client, name, new_auc):
    """Promote the newest version to Production if it clears the floor and
    beats the incumbent. Tags every version with its AUC so future runs can
    compare. Returns True if promoted."""
    candidates = client.get_latest_versions(name, stages=["None"])
    if not candidates:
        print("promote {}: no unstaged version to promote".format(name))
        return False
    version = max(candidates, key=lambda v: int(v.version))
    client.set_model_version_tag(name, version.version, "auc", "{:.6f}".format(new_auc))

    if new_auc < MIN_PRODUCTION_AUC:
        print("skip {}: auc {:.4f} below floor {:.2f}".format(name, new_auc, MIN_PRODUCTION_AUC))
        return False

    current = _production_auc(client, name)
    if current is not None and new_auc < current:
        print("skip {}: auc {:.4f} does not beat production {:.4f}".format(name, new_auc, current))
        return False

    client.transition_model_version_stage(
        name,
        version.version,
        stage="Production",
        archive_existing_versions=True,
    )
    print("promoted {} v{} -> Production (auc {:.4f})".format(name, version.version, new_auc))
    return True


def main():
    tracking_uri = os.environ.get("MLFLOW_TRACKING_URI")
    if tracking_uri:
        mlflow.set_tracking_uri(tracking_uri)
    mlflow.set_experiment(EXPERIMENT_NAME)
    client = MlflowClient()

    df = load_master_from_warehouse()
    X, y = build_features(df)

    with mlflow.start_run():
        mlflow.log_params(MODEL_PARAMS)

        results = {}
        for target_key, y_key in TARGETS.items():
            y_binary = binarize(y[y_key])
            model, metrics, X_test = train_one_model(X, y_binary)
            results[target_key] = metrics

            for metric_name, value in metrics.items():
                mlflow.log_metric("{}_{}".format(target_key, metric_name), value)

            registry_name = "pharmawatch-{}".format(target_key)
            mlflow.lightgbm.log_model(
                lgb_model=model,
                artifact_path="model_{}".format(target_key),
                registered_model_name=registry_name,
            )
            promote_to_production(client, registry_name, metrics["auc"])

            # only run shap on the serious model, too slow for all four
            if target_key == "serious":
                explainer = shap.TreeExplainer(model)
                shap_values = explainer.shap_values(X_test)
                if isinstance(shap_values, list):
                    shap_values = shap_values[1]
                plt.figure()
                shap.summary_plot(shap_values, X_test, show=False)
                plot_path = "shap_summary_serious.png"
                plt.savefig(plot_path, bbox_inches="tight")
                plt.close()
                mlflow.log_artifact(plot_path)

        print("training done")
        for k, m in results.items():
            print("  {}: auc={:.4f} f1={:.4f}".format(k, m["auc"], m["f1"]))


if __name__ == "__main__":
    main()
