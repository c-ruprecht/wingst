import sys, numpy as np, pandas as pd, xgboost as xgb
from sklearn.metrics import roc_auc_score
from features import build, GROUPS, YEARS
df = build(sys.argv[1], sys.argv[2]); df = df[df.year.isin(YEARS)]
FEATS=[c for c in df.columns if c not in ("obs_kn","site","year","daylight")]
P=dict(n_estimators=300,max_depth=4,learning_rate=0.06,subsample=0.8,colsample_bytree=0.9,min_child_weight=5,tree_method="hist",n_jobs=2)
BASE=["icon_ws10","icon_u","icon_v","icon_gust"]
def cv(d,fs,thr):
    a=[]
    for y in YEARS:
        tr,te=d[d.year!=y],d[(d.year==y)&d.daylight]
        m=xgb.XGBClassifier(**P).fit(tr[fs],(tr.obs_kn>=thr).astype(int))
        a.append(roc_auc_score((te.obs_kn>=thr).astype(int),m.predict_proba(te[fs])[:,1]))
    return np.mean(a)
rows=[]
for site in ["darss_ort","barth"]:
    d=df[df.site==site]
    for thr in (12,15):
        b=cv(d,BASE,thr); rows.append(dict(site=site,thr=thr,group="nur ICON (Wind 10 m + Richtung + Böe)",auc=b,gain=0))
        for g,fn in GROUPS.items():
            fs=BASE+[f for f in FEATS if fn(f) and f not in BASE]
            a=cv(d,fs,thr); rows.append(dict(site=site,thr=thr,group=g,auc=a,gain=a-b))
        a=cv(d,FEATS,thr); rows.append(dict(site=site,thr=thr,group="alle Merkmale",auc=a,gain=a-b))
R=pd.DataFrame(rows); R.to_csv(sys.argv[3] + "/forward.csv", index=False)
pd.set_option("display.width",200); print(R.round(4).to_string(index=False))
S=pd.read_csv(sys.argv[3] + "/shap.csv")
S["group"]=S.feature.map(lambda f: next((g for g,fn in GROUPS.items() if fn(f)),"ICON Grundwind"))
G=S.groupby(["site","thr","group"]).mean_abs_shap.sum().groupby(level=[0,1]).apply(lambda s:(s/s.sum()).round(3))
print(G.to_string())
print(S.sort_values("mean_abs_shap",ascending=False).groupby(["site","thr"]).head(8).round(3).to_string(index=False))
