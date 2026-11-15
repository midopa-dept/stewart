"""SWE-bench Verified 고정 부분집합(층화: 저장소 × 난이도, 비례 배분, 시드 20261110). 200 개의 순서 앞 100 개 = sub100(포함 관계).
사용: python3 -I make_subset.py  (입력 swebv-meta.json, 출력 swebv-sub200.txt·swebv-sub100.txt)"""
import json, random
from collections import defaultdict
meta = json.load(open("swebv-meta.json"))
rng = random.Random(20261110)
strata = defaultdict(list)
for iid, (repo, diff) in sorted(meta.items()):
    strata[(repo, diff)].append(iid)
for k in strata:
    rng.shuffle(strata[k])
order = []  # 층마다 차례로 하나씩 뽑되 층 크기에 비례(최대 잔여 비율 우선)
taken = {k: 0 for k in strata}
N = len(meta)
for step in range(200):
    k = max(strata, key=lambda k: (len(strata[k]) * (step + 1) / N - taken[k], -len(strata[k]), k))
    order.append(strata[k][taken[k]]); taken[k] += 1
open("swebv-sub200.txt", "w").write("\n".join(order) + "\n")
open("swebv-sub100.txt", "w").write("\n".join(order[:100]) + "\n")
from collections import Counter
for n in (100, 200):
    c = Counter(meta[i][0] for i in order[:n]); d = Counter(meta[i][1] for i in order[:n])
    print(n, dict(c.most_common(6)), dict(d))
