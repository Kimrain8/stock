"""特征分析：找出优值组和对照组差异显著的特征。

核心输出：
    1. 每个特征的组间对比（均值、中位数、std）
    2. 统计检验（t-test、KS test、AUC）
    3. 按区分度排序的特征榜
    4. 可视化图（箱线图、分布图）

用法：
    python scripts\\analyze_features.py
    python scripts\\analyze_features.py --top 15 --output reports\\features_analysis.md
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import logging
import pickle

import numpy as np
import pandas as pd

from scipy import stats

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
)
logger = logging.getLogger("analyze_features")

BASE_DIR = Path(__file__).resolve().parent.parent
CACHE_DIR = BASE_DIR / "cache" / "features"
REPORTS_DIR = BASE_DIR / "reports"


# ============================================================
def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--lib", default="",
                   help="特征库路径（默认 feature_library.pkl）")
    p.add_argument("--top", type=int, default=20,
                   help="输出 TOP N 特征")
    p.add_argument("--output", default="",
                   help="Markdown 报告输出路径")
    p.add_argument("--plot", action="store_true",
                   help="生成箱线图 PNG")
    return p.parse_args()


# ============================================================
def load_features(path: Path) -> pd.DataFrame:
    with path.open("rb") as f:
        df = pickle.load(f)
    logger.info("loaded feature library | shape=%s", df.shape)
    return df


# ============================================================
def compute_auc(x_pos: np.ndarray, x_neg: np.ndarray) -> float:
    """计算单特征 AUC（不依赖 sklearn）。

    AUC = P(x_pos > x_neg)，随机抽一对正负样本，正样本更大的概率。
    """
    x_pos = x_pos[~np.isnan(x_pos)]
    x_neg = x_neg[~np.isnan(x_neg)]
    if len(x_pos) == 0 or len(x_neg) == 0:
        return np.nan

    # 用 Mann-Whitney U 统计量计算 AUC
    u_stat, _ = stats.mannwhitneyu(x_pos, x_neg, alternative="two-sided")
    auc = u_stat / (len(x_pos) * len(x_neg))
    return float(auc)


# ============================================================
def analyze_one_feature(df: pd.DataFrame, col: str) -> dict:
    """对单个特征做完整分析。"""
    valid = df[df["label"] == 1][col].dropna().to_numpy()
    control = df[df["label"] == 0][col].dropna().to_numpy()

    if len(valid) < 10 or len(control) < 10:
        return None

    v_mean = float(np.mean(valid))
    c_mean = float(np.mean(control))
    v_med = float(np.median(valid))
    c_med = float(np.median(control))
    v_std = float(np.std(valid, ddof=1))
    c_std = float(np.std(control, ddof=1))

    # t-test（Welch，不假设方差相等）
    try:
        t_stat, t_p = stats.ttest_ind(valid, control, equal_var=False)
        t_p = float(t_p)
    except Exception:
        t_stat, t_p = np.nan, np.nan

    # KS test
    try:
        ks_stat, ks_p = stats.ks_2samp(valid, control)
        ks_stat = float(ks_stat)
        ks_p = float(ks_p)
    except Exception:
        ks_stat, ks_p = np.nan, np.nan

    # AUC
    auc = compute_auc(valid, control)

    # 标准化均值差异（Cohen's d）
    pooled_std = np.sqrt((v_std ** 2 + c_std ** 2) / 2)
    cohens_d = (v_mean - c_mean) / pooled_std if pooled_std > 0 else np.nan

    # 区分度评分：AUC 偏离 0.5 越远越好；同时考虑 KS 统计量
    if np.isnan(auc):
        score = 0.0
    else:
        score = abs(auc - 0.5) * 2.0   # 0~1
    score *= (1.0 - (ks_p if not np.isnan(ks_p) else 1.0))

    return {
        "feature": col,
        "n_valid": len(valid),
        "n_control": len(control),
        "valid_mean": v_mean,
        "control_mean": c_mean,
        "valid_median": v_med,
        "control_median": c_med,
        "valid_std": v_std,
        "control_std": c_std,
        "diff_mean": v_mean - c_mean,
        "cohens_d": cohens_d,
        "t_stat": float(t_stat) if not np.isnan(t_stat) else np.nan,
        "t_p": t_p,
        "ks_stat": ks_stat,
        "ks_p": ks_p,
        "auc": auc,
        "score": score,
    }


# ============================================================
def analyze_all(df: pd.DataFrame) -> pd.DataFrame:
    front = {"ts_code", "signal_date", "label"}
    feat_cols = [c for c in df.columns if c not in front]
    logger.info("analyzing %d features ...", len(feat_cols))

    rows = []
    for col in feat_cols:
        r = analyze_one_feature(df, col)
        if r:
            rows.append(r)

    result = pd.DataFrame(rows)
    result = result.sort_values("score", ascending=False).reset_index(drop=True)
    return result


# ============================================================
def print_report(result: pd.DataFrame, top: int):
    """打印控制台报告。"""
    print(f"\n{'=' * 100}")
    print(f"特征区分度排行（按 score 排序）")
    print(f"{'=' * 100}")
    print(f"{'排名':<4} {'特征':<22} {'优值均值':>12} {'对照均值':>12} "
          f"{'差异':>10} {'Cohen_d':>10} {'KS_p':>8} {'AUC':>8} {'score':>7}")
    print("-" * 100)

    for i, row in result.head(top).iterrows():
        print(f"{i+1:<4} {row['feature']:<22} "
              f"{row['valid_mean']:>12.4f} {row['control_mean']:>12.4f} "
              f"{row['diff_mean']:>+10.4f} "
              f"{row['cohens_d']:>+10.3f} "
              f"{row['ks_p']:>8.4f} "
              f"{row['auc']:>8.4f} "
              f"{row['score']:>7.4f}")

    print(f"\n{'=' * 100}")
    print(f"关键解读")
    print(f"{'=' * 100}")

    top5 = result.head(5)
    for _, r in top5.iterrows():
        direction = "↑" if r["diff_mean"] > 0 else "↓"
        print(f"\n★ {r['feature']}")
        print(f"  优值组均值：{r['valid_mean']:.4f}，"
              f"对照组均值：{r['control_mean']:.4f}")
        print(f"  方向：{direction}  差异：{r['diff_mean']:+.4f}  "
              f"Cohen's d：{r['cohens_d']:+.3f}")
        print(f"  AUC：{r['auc']:.4f}  "
              f"（0.5=无区分，>0.6 或 <0.4 才有意义）")


# ============================================================
def save_markdown(result: pd.DataFrame, top: int, path: Path):
    """输出 Markdown 报告。"""
    lines = []
    lines.append("# 特征分析报告\n")
    lines.append(f"**分析时间**：{pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}\n")
    lines.append(f"**样本数**：优值 200 + 对照 594 = 794\n")
    lines.append(f"**特征数**：{len(result)}\n")
    lines.append("\n## 一、区分度排行 TOP {}\n".format(top))

    lines.append("\n| 排名 | 特征 | 优值均值 | 对照均值 | 差异 | Cohen's d | AUC | score |")
    lines.append("| :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: |")
    for i, r in result.head(top).iterrows():
        lines.append(
            f"| {i+1} | `{r['feature']}` | {r['valid_mean']:.4f} | "
            f"{r['control_mean']:.4f} | {r['diff_mean']:+.4f} | "
            f"{r['cohens_d']:+.3f} | {r['auc']:.4f} | {r['score']:.4f} |"
        )

    lines.append("\n## 二、关键发现\n")
    for _, r in result.head(5).iterrows():
        d = "↑" if r["diff_mean"] > 0 else "↓"
        lines.append(f"### {r['feature']}")
        lines.append(f"- 优值组均值：{r['valid_mean']:.4f}")
        lines.append(f"- 对照组均值：{r['control_mean']:.4f}")
        lines.append(f"- 差异方向：{d} {r['diff_mean']:+.4f}")
        lines.append(f"- Cohen's d：{r['cohens_d']:+.3f}")
        lines.append(f"- AUC：{r['auc']:.4f}")
        lines.append("")

    lines.append("\n## 三、完整排行\n")
    lines.append("| 特征 | 优值均值 | 对照均值 | 差异 | Cohen's d | t_p | KS_p | AUC |")
    lines.append("| :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for _, r in result.iterrows():
        lines.append(
            f"| `{r['feature']}` | {r['valid_mean']:.4f} | "
            f"{r['control_mean']:.4f} | {r['diff_mean']:+.4f} | "
            f"{r['cohens_d']:+.3f} | {r['t_p']:.4f} | "
            f"{r['ks_p']:.4f} | {r['auc']:.4f} |"
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("markdown saved | %s", path)


# ============================================================
def plot_boxplots(result: pd.DataFrame, df: pd.DataFrame, top: int,
                  out_dir: Path):
    """生成 TOP N 特征的箱线图。"""
    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots
    except ImportError:
        logger.warning("plotly 未安装，跳过绘图")
        return

    top_feats = result.head(top)["feature"].tolist()
    n = len(top_feats)
    rows = (n + 3) // 4
    cols = min(4, n)

    fig = make_subplots(
        rows=rows, cols=cols,
        subplot_titles=top_feats,
        vertical_spacing=0.08,
    )

    for i, feat in enumerate(top_feats):
        r = i // cols + 1
        c = i % cols + 1

        v = df[df["label"] == 1][feat].dropna().to_numpy()
        ct = df[df["label"] == 0][feat].dropna().to_numpy()

        fig.add_trace(
            go.Box(y=v, name="优值", marker_color="#e53935",
                   boxmean=True, showlegend=(i == 0)),
            row=r, col=c,
        )
        fig.add_trace(
            go.Box(y=ct, name="对照", marker_color="#1e88e5",
                   boxmean=True, showlegend=(i == 0)),
            row=r, col=c,
        )

    fig.update_layout(
        height=300 * rows,
        title=f"TOP {top} 特征：优值组 vs 对照组",
        showlegend=True,
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "feature_boxplots.html"
    fig.write_html(str(out_path))
    logger.info("boxplot saved | %s", out_path)


# ============================================================
def main():
    args = parse_args()

    lib_path = Path(args.lib) if args.lib else CACHE_DIR / "feature_library.pkl"
    if not lib_path.exists():
        logger.error("特征库不存在 | %s", lib_path)
        return

    logger.info("=" * 60)
    logger.info("analyze_features | lib=%s | top=%d",
                lib_path.name, args.top)
    logger.info("=" * 60)

    df = load_features(lib_path)
    result = analyze_all(df)

    print_report(result, args.top)

    # 保存结果
    out_path = CACHE_DIR / "feature_analysis.pkl"
    with out_path.open("wb") as f:
        pickle.dump(result, f)
    logger.info("result saved | %s", out_path)

    # Markdown 报告
    if args.output:
        save_markdown(result, args.top, Path(args.output))

    # 绘图
    if args.plot:
        plot_boxplots(result, df, args.top, REPORTS_DIR)


if __name__ == "__main__":
    main()