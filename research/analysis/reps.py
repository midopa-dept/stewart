"""연구 담당: E13 v2 과제별 분산으로 X1-01 회차 수 어림. 사용: python3 -I reps.py <merged 폴더 A> <merged 폴더 B> ..."""
import json, sys, os, math, random, statistics as st
def load(d):
    t = {}
    for l in open(os.path.join(d, 'results.jsonl')):
        r = json.loads(l); t.setdefault(r['task'], []).append(1 if r['success'] else 0)
    return t
conds = {os.path.basename(d).split('-m4-')[1].replace('-v2-merged', ''): load(d) for d in sys.argv[1:]}
names = list(conds); tasks = sorted(set.intersection(*[set(c) for c in conds.values()])); T = len(tasks)
p = {n: {t: sum(conds[n][t]) / len(conds[n][t]) for t in tasks} for n in names}
within = st.mean(p[n][t] * (1 - p[n][t]) for n in names for t in tasks) * 3 / 2   # 3회 표본 → 불편 보정
print(f"tasks={T}, 평균 회차 내 분산 σ²≈{within:.3f}")
print("과제별 p 분포(조건마다 0·1 아닌 과제 수):", {n: sum(0 < p[n][t] < 1 for t in tasks) for n in names})
def pair(a, b):
    d = [p[a][t] - p[b][t] for t in tasks]
    var_obs = st.variance(d); tau2 = max(var_obs - 2 * within / 3, 0)
    rnd = random.Random(1); boots = []
    for _ in range(10000):
        s = [rnd.choice(d) for _ in tasks]; boots.append(sum(s) / T)
    boots.sort()
    return st.mean(d), boots[249], boots[9749], tau2
print("\n짝 차이(관측 3회): 평균, 과제 군집 부트스트랩 95% CI, τ²(과제 간 참 차이 분산)")
pairs = [(a, b) for i, a in enumerate(names) for b in names[i+1:]]
taus = []
for a, b in pairs:
    m, lo, hi, tau2 = pair(a, b); taus.append(tau2)
    print(f"  {a:>20s} − {b:<20s} {m*100:+6.1f}pt  [{lo*100:+.1f}, {hi*100:+.1f}]  τ²={tau2:.3f}")
tau2 = st.median(taus)
print(f"\nτ² 중앙값 {tau2:.3f} 로 투영한 95% CI 반폭(pt): (과제 32를 표본으로 볼 때 / 과제 고정으로 볼 때)")
for r in (3, 5, 8, 10):
    hw_rand = 1.96 * math.sqrt(tau2 + 2 * within / r) / math.sqrt(T) * 100
    hw_fix = 1.96 * math.sqrt(2 * within / r) / math.sqrt(T) * 100
    hw_int = 1.96 * math.sqrt(2 * tau2 + 4 * within / r) / math.sqrt(T) * 100
    print(f"  r={r:2d}: 짝 차이 ±{hw_rand:.1f} / ±{hw_fix:.1f}   상호작용(차이의 차이) ±{hw_int:.1f} / ±{1.96*math.sqrt(4*within/r)/math.sqrt(T)*100:.1f}")
