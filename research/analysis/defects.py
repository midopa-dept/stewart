"""연구 담당: 평가 기록에서 실행별 결함 지표를 뽑는다(읽기만). 사용: python3 -I defects.py <merged 폴더>..."""
import json, sys, collections, os

def run_metrics(r):
    m = dict(task=r['task'], group=r['group'], rep=r['rep'], success=bool(r['success']),
             stop=r['stop_reason'], steps=r['steps'], parse_fail=r['parse_failures'],
             tool_err=r['tool_errors'], q_verdict=r.get('q_verdict'), q_expect=r.get('q_expect'))
    s = json.load(open(os.path.join(r['run_dir'], 'summary.json')))
    m.update(length_stops=s.get('length_stops', 0) or 0, format_fail=s.get('format_failures', 0) or 0,
             repeat_nudges=s.get('repeat_nudges', 0) or 0, lang_retry=s.get('language_retries', 0) or 0)
    fin = collections.Counter(); len_empty = 0; stop_empty = 0; dup_ok = 0; prev = None; rollbacks = 0
    for line in open(os.path.join(r['run_dir'], 'events.jsonl')):
        e = json.loads(line)
        if e['kind'] == 'rollback': rollbacks += 1
        if e['kind'] != 'step': continue
        resp = e.get('response') or {}
        fr = resp.get('finish_reason'); fin[fr] += 1
        tcs = resp.get('tool_calls') or []
        empty = not (resp.get('content') or '').strip() and not tcs
        if fr == 'length' and empty: len_empty += 1
        if fr == 'stop' and empty: stop_empty += 1
        sig = json.dumps([(t.get('function') or {}).get('name') for t in tcs] +
                         [(t.get('function') or {}).get('arguments') for t in tcs], sort_keys=True) if tcs else None
        if sig and sig == prev: dup_ok += 1
        prev = sig
    m.update(finish_length=fin.get('length', 0), length_empty=len_empty, stop_empty=stop_empty,
             consecutive_dup_calls=dup_ok, rollbacks=rollbacks)
    return m

if __name__ == '__main__':
    for d in sys.argv[1:]:
        rows = [run_metrics(json.loads(l)) for l in open(os.path.join(d, 'results.jsonl'))]
        n = len(rows)
        agg = lambda k: sum(1 for x in rows if x[k]) / n
        tot = lambda k: sum(x[k] for x in rows)
        print(f"{os.path.basename(d)[-42:]:42s} n={n} succ={agg('success'):.3f} "
              f"runs_w_len={agg('finish_length'):.3f} len_empty={tot('length_empty')} stop_empty={tot('stop_empty')} "
              f"parse={tot('parse_fail')} fmt={tot('format_fail')} toolerr/run={tot('tool_err')/n:.2f} "
              f"dup_calls={tot('consecutive_dup_calls')} nudges={tot('repeat_nudges')} maxsteps={sum(x['stop']=='max_steps' for x in rows)} "
              f"consec_err={sum(x['stop']=='consecutive_errors' for x in rows)} asked={sum(x['stop']=='asked' for x in rows)} "
              f"rollbacks={tot('rollbacks')} lang={tot('lang_retry')}")
        out = os.path.join(os.environ.get('OUT', '.'), os.path.basename(d) + '.defects.jsonl')
        with open(out, 'w') as fh:
            for x in rows: fh.write(json.dumps(x, ensure_ascii=False) + '\n')
