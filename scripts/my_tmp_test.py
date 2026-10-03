import pickle, numpy as np
with open("cache/features/money_model.pkl", "rb") as f:
    pkg = pickle.load(f)

m1 = pkg["model_2025to2026"]
m2 = pkg["model_2026to2025"]

# 加载 2025 训练集
with open("cache/features/feature_library_2025.pkl", "rb") as f:
    df = pickle.load(f)

feats = pkg["features"]
X = df[feats].fillna(0)
y = df["label"].values

p1 = m1.predict(X)
p2 = m2.predict(X)
p_avg = (p1 + p2) / 2

print(f"优值组（label=1）：n={sum(y==1)}  "
      f"mean={p_avg[y==1].mean():.4f}  "
      f"median={np.median(p_avg[y==1]):.4f}  "
      f"min={p_avg[y==1].min():.4f}  max={p_avg[y==1].max():.4f}")
print(f"对照组（label=0）：n={sum(y==0)}  "
      f"mean={p_avg[y==0].mean():.4f}  "
      f"median={np.median(p_avg[y==0]):.4f}  "
      f"min={p_avg[y==0].min():.4f}  max={p_avg[y==0].max():.4f}")