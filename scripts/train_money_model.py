"""训练 LightGBM 资金意图模型。

策略：
    1. 2025 训练 → 2026 验证
    2. 2026 训练 → 2025 验证
    3. 两次取平均作为最终模型

输入：
    cache/features/feature_library_2025.pkl
    cache/features/feature_library_2026.pkl

输出：
    cache/features/money_model.pkl
    reports/money_model_report.md
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import logging
import pickle
import time

import numpy as np
import pandas as pd
import lightgbm as lgb
from sklearn.metrics import (
    roc_auc_score, accuracy_score, precision_score, recall_score,
    f1_score,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
)
logger = logging.getLogger("train_money_model")

BASE_DIR = Path(__file__).resolve().parent.parent
CACHE_DIR = BASE_DIR / "cache" / "features"
REPORTS_DIR = BASE_DIR / "reports"


CORE_FEATURES = [
    "std_ratio_20",
    "std_ratio_10",
    "amount_last",
    "limit_up_20",
    "big_vol_days_20",
    "big_vol_days_10",
    "vol_ratio_v_10",
    "vol_ratio_v_20",
    "small_vol_days_10",
    "small_vol_days_5",
]


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--n-estimators", type=int, default=200)
    p.add_argument("--learning-rate", type=float, default=0.05)
    p.add_argument("--max-depth", type=int, default=5)
    p.add_argument("--num-leaves", type=int, default=15)
    p.add_argument("--min-child-samples", type=int, default=20)
    p.add_argument("--seed", type=int, default=42)
    return p.parse_args()


def load_features(year: int) -> pd.DataFrame | None:
    candidates = [
        CACHE_DIR / f"feature_library_{year}.pkl",
        CACHE_DIR / "feature_library.pkl",
    ]
    for p in candidates:
        if p.exists():
            with p.open("rb") as f:
                df = pickle.load(f)
            logger.info("loaded %s | shape=%s", p.name, df.shape)
            return df
    logger.warning("no feature library for year=%d", year)
    return None


def prepare_xy(df: pd.DataFrame) -> tuple:
    missing = [c for c in CORE_FEATURES if c not in df.columns]
    if missing:
        logger.warning("missing features: %s", missing)
    avail = [c for c in CORE_FEATURES if c in df.columns]
    X = df[avail].copy()
    y = df["label"].astype(int).values
    for c in avail:
        med = X[c].median()
        if pd.isna(med):
            med = 0.0
        X[c] = X[c].fillna(med)
    return X, y, avail


def train_one(X_train, y_train, X_val, y_val, args, direction: str) -> tuple:
    logger.info("=" * 60)
    logger.info("训练方向：%s", direction)
    logger.info("  train: n=%d (pos=%d, neg=%d)",
                len(X_train), int(y_train.sum()), int((1 - y_train).sum()))
    logger.info("  valid: n=%d (pos=%d, neg=%d)",
                len(X_val), int(y_val.sum()), int((1 - y_val).sum()))
    logger.info("=" * 60)

    train_data = lgb.Dataset(X_train, label=y_train)
    val_data = lgb.Dataset(X_val, label=y_val, reference=train_data)

    params = {
        "objective": "binary",
        "metric": "auc",
        "boosting_type": "gbdt",
        "num_leaves": args.num_leaves,
        "max_depth": args.max_depth,
        "learning_rate": args.learning_rate,
        "min_child_samples": args.min_child_samples,
        "feature_fraction": 0.8,
        "bagging_fraction": 0.8,
        "bagging_freq": 5,
        "seed": args.seed,
        "verbose": -1,
    }

    callbacks = [
        lgb.early_stopping(stopping_rounds=30, verbose=False),
        lgb.log_evaluation(period=50),
    ]

    model = lgb.train(
        params, train_data,
        num_boost_round=args.n_estimators,
        valid_sets=[val_data],
        callbacks=callbacks,
    )

    y_prob = model.predict(X_val, num_iteration=model.best_iteration)
    y_pred = (y_prob >= 0.5).astype(int)

    try:
        auc = roc_auc_score(y_val, y_prob)
    except Exception:
        auc = np.nan

    metrics = {
        "direction": direction,
        "n_train": len(X_train),
        "n_val": len(X_val),
        "best_iteration": int(model.best_iteration or 0),
        "auc": float(auc),
        "accuracy": float(accuracy_score(y_val, y_pred)),
        "precision": float(precision_score(y_val, y_pred, zero_division=0)),
        "recall": float(recall_score(y_val, y_pred, zero_division=0)),
        "f1": float(f1_score(y_val, y_pred, zero_division=0)),
    }

    logger.info("  best_iteration=%d | AUC=%.4f | Acc=%.4f | F1=%.4f",
                metrics["best_iteration"], metrics["auc"],
                metrics["accuracy"], metrics["f1"])
    return model, metrics


def feature_importance(model, feature_names: list) -> pd.DataFrame:
    imp_gain = model.feature_importance(importance_type="gain")
    imp_split = model.feature_importance(importance_type="split")
    total_gain = imp_gain.sum() if imp_gain.sum() > 0 else 1
    return pd.DataFrame({
        "feature": feature_names,
        "gain": imp_gain,
        "gain_pct": imp_gain / total_gain * 100,
        "split": imp_split,
    }).sort_values("gain", ascending=False).reset_index(drop=True)


def save_report(m1, m2, avg_auc, imp1, imp2, path: Path):
    lines = []
    lines.append("# 资金意图模型 — 训练报告\n")
    lines.append(f"**训练时间**：{pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}\n")
    lines.append(f"**特征数**：{len(CORE_FEATURES)}\n")
    lines.append(f"**平均 AUC**：{avg_auc:.4f}\n")

    lines.append("\n## 一、两个方向的模型\n")
    lines.append("| 方向 | 训练 | 验证 | best_iter | AUC | Acc | Precision | Recall | F1 |")
    lines.append("| :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for m in [m1, m2]:
        lines.append(
            f"| {m['direction']} | {m['n_train']} | {m['n_val']} | "
            f"{m['best_iteration']} | {m['auc']:.4f} | {m['accuracy']:.4f} | "
            f"{m['precision']:.4f} | {m['recall']:.4f} | {m['f1']:.4f} |"
        )

    lines.append("\n## 二、特征重要性（gain %）\n")
    lines.append("| 特征 | 2025→2026 gain% | 2026→2025 gain% | 平均 gain% |")
    lines.append("| :--- | ---: | ---: | ---: |")
    merged = imp1[["feature", "gain_pct"]].rename(
        columns={"gain_pct": "imp1"}
    ).merge(
        imp2[["feature", "gain_pct"]].rename(columns={"gain_pct": "imp2"}),
        on="feature", how="outer",
    ).fillna(0)
    merged["avg"] = (merged["imp1"] + merged["imp2"]) / 2
    merged = merged.sort_values("avg", ascending=False)
    for _, r in merged.iterrows():
        lines.append(
            f"| `{r['feature']}` | {r['imp1']:.2f} | {r['imp2']:.2f} | "
            f"{r['avg']:.2f} |"
        )

    lines.append("\n## 三、解读\n")
    if avg_auc >= 0.70:
        lines.append(f"**AUC = {avg_auc:.4f}**：模型区分度**强**。")
    elif avg_auc >= 0.60:
        lines.append(f"**AUC = {avg_auc:.4f}**：模型区分度**中等**，可用。")
    elif avg_auc >= 0.55:
        lines.append(f"**AUC = {avg_auc:.4f}**：模型区分度**弱**，谨慎使用。")
    else:
        lines.append(f"**AUC = {avg_auc:.4f}**：模型**基本无效**。")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("report saved | %s", path)


def main():
    args = parse_args()

    logger.info("=" * 60)
    logger.info("train_money_model | n_est=%d | lr=%.3f | depth=%d | leaves=%d",
                args.n_estimators, args.learning_rate,
                args.max_depth, args.num_leaves)
    logger.info("=" * 60)

    t0 = time.time()

    df_2025 = load_features(2025)
    df_2026 = load_features(2026)
    if df_2025 is None or df_2026 is None:
        logger.error("需要两个年份的特征库")
        return

    X25, y25, avail = prepare_xy(df_2025)
    X26, y26, _ = prepare_xy(df_2026)
    logger.info("features used (%d): %s", len(avail), avail)

    model1, m1 = train_one(X25, y25, X26, y26, args, "2025 → 2026")
    model2, m2 = train_one(X26, y26, X25, y25, args, "2026 → 2025")

    imp1 = feature_importance(model1, avail)
    imp2 = feature_importance(model2, avail)

    print(f"\n{'=' * 80}")
    print(f"特征重要性（gain %）")
    print(f"{'=' * 80}")
    print(f"{'特征':<22} {'2025→2026':>12} {'2026→2025':>12} {'平均':>10}")
    print("-" * 80)
    merged = imp1[["feature", "gain_pct"]].rename(
        columns={"gain_pct": "imp1"}
    ).merge(
        imp2[["feature", "gain_pct"]].rename(columns={"gain_pct": "imp2"}),
        on="feature", how="outer",
    ).fillna(0)
    merged["avg"] = (merged["imp1"] + merged["imp2"]) / 2
    merged = merged.sort_values("avg", ascending=False)
    for _, r in merged.iterrows():
        print(f"{r['feature']:<22} {r['imp1']:>12.2f} "
              f"{r['imp2']:>12.2f} {r['avg']:>10.2f}")

    avg_auc = (m1["auc"] + m2["auc"]) / 2
    print(f"\n{'=' * 80}")
    print(f"模型性能")
    print(f"{'=' * 80}")
    print(f"方向 1（2025→2026）：AUC = {m1['auc']:.4f} | F1 = {m1['f1']:.4f}")
    print(f"方向 2（2026→2025）：AUC = {m2['auc']:.4f} | F1 = {m2['f1']:.4f}")
    print(f"平均 AUC：{avg_auc:.4f}")

    # 训练集预测分布诊断
    print(f"\n--- 训练集预测分布诊断 ---")
    for name, model, X, y in [("2025 训练集", model1, X25, y25),
                              ("2026 训练集", model2, X26, y26)]:
        p = model.predict(X)
        pos = p[y == 1]
        neg = p[y == 0]
        print(f"{name}:")
        print(f"  优值组：mean={pos.mean():.4f} median={np.median(pos):.4f} "
              f"max={pos.max():.4f}")
        print(f"  对照组：mean={neg.mean():.4f} median={np.median(neg):.4f} "
              f"max={neg.max():.4f}")

    model_pkg = {
        "model_2025to2026": model1,
        "model_2026to2025": model2,
        "features": avail,
        "avg_auc": avg_auc,
        "metrics": {"2025to2026": m1, "2026to2025": m2},
        "feature_importance": {
            "2025to2026": imp1.to_dict(orient="records"),
            "2026to2025": imp2.to_dict(orient="records"),
        },
        "trained_at": pd.Timestamp.now().isoformat(),
    }

    out_path = CACHE_DIR / "money_model.pkl"
    with out_path.open("wb") as f:
        pickle.dump(model_pkg, f)
    logger.info("model saved | %s", out_path)

    save_report(m1, m2, avg_auc, imp1, imp2,
                REPORTS_DIR / "money_model_report.md")
    logger.info("total time | %.1fs", time.time() - t0)


if __name__ == "__main__":
    main()