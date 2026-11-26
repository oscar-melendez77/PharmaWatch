import json
import os
from datetime import datetime, timezone
from pathlib import Path

import lightgbm as lgb
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import mlflow
import mlflow.lightgbm
import numpy as np
import pandas as pd
from dotenv import load_dotenv
from google.cloud import bigquery
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
from sklearn.model_selection import GroupKFold

# 1. config
load_dotenv()
PROJECT_ID = os.environ["GCP_PROJECT_ID"]
BQ_LOCATION = os.environ["BQ_LOCATION"]
TABLE = PROJECT_ID + ".pharmawatch_marts.mart_risk_features"

TARGET = "is_serious"
CATEGORICAL = ["drug_name", "patient_sex"]
NUMERIC = ["patient_age_years", "patient_weight_kg", "n_drugs"]

N_SPLITS = 5
RANDOM_SEED = 42
EARLY_STOPPING_ROUNDS = 30
PARAMS = {
    "n_estimators": 500,
    "learning_rate": 0.03,
    "num_leaves": 15,
    "min_child_samples": 40,
    "subsample": 0.8,
    "subsample_freq": 1,
    "colsample_bytree": 0.8,
    "reg_lambda": 5.0,
    "random_state": RANDOM_SEED,
    "verbose": -1,
}

ARTIFACT_DIR = Path("ml/artifacts")
ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
print("[1/7] config ready, target", TARGET, "features", CATEGORICAL + NUMERIC)

# 2. read the training table from BigQuery
bq_client = bigquery.Client(project=PROJECT_ID, location=BQ_LOCATION)
df = bq_client.query("select * from `" + TABLE + "`").to_dataframe()
df = df.reset_index(drop=True)
print("[2/7] read", len(df), "rows,", df["report_id"].nunique(), "reports,", df["drug_name"].nunique(), "drugs from", TABLE)

# 3. prepare the features
features = df[CATEGORICAL + NUMERIC].copy()
for column in CATEGORICAL:
    features[column] = features[column].astype("category")
for column in NUMERIC:
    features[column] = features[column].astype(float)

target = df[TARGET].astype(int)
groups = df["report_id"]
base_rate = float(target.mean())
print("[3/7] share of serious reports:", round(base_rate, 4))
print("      share of rows missing each feature:", {column: round(float(features[column].isna().mean()), 3) for column in features.columns})

# 4. cross validation, every fold keeps all rows of a report together
splitter = GroupKFold(n_splits=N_SPLITS)
model_pred = np.zeros(len(df))
baseline_pred = np.zeros(len(df))
best_iterations = []

for fold, (train_idx, valid_idx) in enumerate(splitter.split(features, target, groups)):
    x_train = features.iloc[train_idx]
    x_valid = features.iloc[valid_idx]
    y_train = target.iloc[train_idx]
    y_valid = target.iloc[valid_idx]

    model = lgb.LGBMClassifier(**PARAMS)
    model.fit(
        x_train,
        y_train,
        eval_set=[(x_valid, y_valid)],
        eval_metric="binary_logloss",
        callbacks=[lgb.early_stopping(EARLY_STOPPING_ROUNDS, verbose=False)],
    )
    model_pred[valid_idx] = model.predict_proba(x_valid)[:, 1]
    best_iterations.append(model.best_iteration_)

    # the baseline only knows how often reports of the same drug were serious in the training part
    drug_rate = {str(drug): rate for drug, rate in y_train.groupby(x_train["drug_name"].astype(str)).mean().items()}
    baseline_pred[valid_idx] = [drug_rate.get(str(drug), float(y_train.mean())) for drug in x_valid["drug_name"]]
    print("      fold", fold + 1, "of", N_SPLITS, "done, best iteration", model.best_iteration_)

metrics = {
    "model_auc": float(roc_auc_score(target, model_pred)),
    "baseline_auc": float(roc_auc_score(target, baseline_pred)),
    "model_log_loss": float(log_loss(target, model_pred)),
    "baseline_log_loss": float(log_loss(target, baseline_pred)),
    "model_brier": float(brier_score_loss(target, model_pred)),
    "baseline_brier": float(brier_score_loss(target, baseline_pred)),
    "rows": int(len(df)),
    "reports": int(df["report_id"].nunique()),
    "share_serious": base_rate,
}
print("[4/7] out of fold results (the baseline is the serious share of each drug)")
print("      AUC       model", round(metrics["model_auc"], 4), "| baseline", round(metrics["baseline_auc"], 4))
print("      log loss  model", round(metrics["model_log_loss"], 4), "| baseline", round(metrics["baseline_log_loss"], 4))
print("      brier     model", round(metrics["model_brier"], 4), "| baseline", round(metrics["baseline_brier"], 4))

# does a predicted 40 percent really mean about 40 percent of such reports were serious
bins = pd.qcut(model_pred, 10, duplicates="drop")
calibration = (
    pd.DataFrame({"predicted": model_pred, "actual": target.values, "bin": bins})
    .groupby("bin", observed=True)
    .agg(predicted=("predicted", "mean"), actual=("actual", "mean"), rows=("actual", "size"))
    .reset_index(drop=True)
)
print("      calibration by tenth of predictions:")
print(calibration.round(3).to_string(index=False))

fig, ax = plt.subplots(figsize=(5, 5))
ax.plot([0, 1], [0, 1], linestyle="--", color="grey")
ax.plot(calibration["predicted"], calibration["actual"], marker="o")
ax.set_xlabel("predicted share of serious reports")
ax.set_ylabel("actual share of serious reports")
ax.set_title("Calibration (out of fold)")
fig.tight_layout()
fig.savefig(ARTIFACT_DIR / "calibration.png", dpi=150)
plt.close(fig)

# 5. final model on all rows, the number of trees is the average the folds chose
final_params = dict(PARAMS)
final_params["n_estimators"] = max(int(np.mean(best_iterations)), 10)
final_model = lgb.LGBMClassifier(**final_params)
final_model.fit(features, target)
print("[5/7] final model trained with", final_params["n_estimators"], "trees")

# 6. explain with SHAP values, LightGBM computes them exactly for tree models
contributions = final_model.predict(features, pred_contrib=True)
shap_values = contributions[:, :-1]
importance = pd.Series(np.abs(shap_values).mean(axis=0), index=features.columns).sort_values()
print("[6/7] mean absolute SHAP value per feature (log odds):")
print(importance.sort_values(ascending=False).round(4).to_string())

fig, ax = plt.subplots(figsize=(7, 4))
importance.plot(kind="barh", ax=ax)
ax.set_xlabel("mean absolute SHAP value (log odds)")
ax.set_title("What moves the predicted share of serious reports")
fig.tight_layout()
fig.savefig(ARTIFACT_DIR / "shap_importance.png", dpi=150)
plt.close(fig)

# one example, how each feature pushed the prediction for a single amphetamine report
example_row = 0
example_candidates = df.index[(df["drug_name"] == "amphetamine") & df["patient_age_years"].notna()]
if len(example_candidates) > 0:
    example_row = int(example_candidates[0])
example = pd.Series(shap_values[example_row], index=features.columns)
print("      example report", df.loc[example_row, "report_id"], df.loc[example_row, "drug_name"], "- push on the log odds from each feature:")
print(example.round(3).to_string())

# 7. save the model and track the run
final_model.booster_.save_model(str(ARTIFACT_DIR / "risk_model.txt"))

feature_info = {
    "features": list(features.columns),
    "categorical": CATEGORICAL,
    "categories": {column: [str(value) for value in features[column].cat.categories] for column in CATEGORICAL},
    "target": TARGET,
    "share_serious": base_rate,
    "trained_at": datetime.now(timezone.utc).isoformat(),
}
with open(ARTIFACT_DIR / "feature_info.json", "w") as f:
    json.dump(feature_info, f, indent=2)
with open(ARTIFACT_DIR / "metrics.json", "w") as f:
    json.dump(metrics, f, indent=2)

mlflow.set_tracking_uri(Path("mlruns").resolve().as_uri())
mlflow.set_experiment("pharmawatch-risk-score")
with mlflow.start_run(run_name="lightgbm-share-of-serious-reports"):
    mlflow.log_params(final_params)
    mlflow.log_param("n_splits", N_SPLITS)
    mlflow.log_param("features", ",".join(features.columns))
    mlflow.log_metrics(metrics)
    for file_name in ["risk_model.txt", "feature_info.json", "metrics.json", "shap_importance.png", "calibration.png"]:
        mlflow.log_artifact(str(ARTIFACT_DIR / file_name))
    mlflow.lightgbm.log_model(final_model.booster_, artifact_path="model")

print("[7/7] saved the model and metrics in", ARTIFACT_DIR, "and logged the run in mlruns")
