import sqlite3, os, json, statistics as st
from collections import Counter, defaultdict
import datetime as dt

p = os.path.expandvars(r'%APPDATA%/aragon-desktop/aragon.db')
c = sqlite3.connect('file:' + p + '?mode=ro', uri=True)
q = '''select t.id,t.run_id,t.ts,t.side,t.price,t.qty,t.fee,t.pnl,t.position_after,t.explain_json,
r.inst_id,r.bar,r.group_id,r.created_at,t.stop_loss_price,t.take_profit_price,t.reason
from trades t join strategy_runs r on r.id=t.run_id where r.mode="ai-trade" order by t.id'''
rows = c.execute(q).fetchall()
recs = []
for r in rows:
    ej = json.loads(r[9]) if r[9] else {}
    m = ej.get('meta') or {}
    recs.append(dict(id=r[0], run=r[1], ts=r[2], side=r[3], px=r[4], qty=r[5], fee=r[6], pnl=r[7],
                     pos=r[8], inst=r[10], bar=r[11], grp=r[12], created=r[13],
                     sl=r[14], tp=r[15], reason=(r[16] or '')[:60], kind=ej.get('kind'), meta=m))

# ---- group-level (session) aggregation
by_grp = defaultdict(list)
for x in recs: by_grp[x['grp']].append(x)
print('== sessions (group_id) ==')
for g, v in sorted(by_grp.items(), key=lambda kv: min(y['id'] for kv2 in [kv[1]] for y in kv2)):
    closes = [y for y in v if abs(y['pnl']) > 1e-12]
    pnl = sum(y['pnl'] for y in v); fee = sum(y['fee'] for y in v)
    t0 = min(y['created'] for y in v); t1 = max(y['created'] for y in v)
    print('  %-46s legs=%-4d closes=%-4d pnl=%8.2f fee=%7.2f  %s -> %s' % (
        (g or 'None')[:46], len(v), len(closes), pnl, fee, t0[:16], t1[:16]))

print('\n== churn: reopen after close on same run ==')
byrun = defaultdict(list)
for x in recs: byrun[x['run']].append(x)
gaps = []
for run, v in byrun.items():
    v.sort(key=lambda y: y['id'])
    last_close_ts = None
    for y in v:
        if abs(y['pnl']) > 1e-12 or y['pos'] == 0:
            last_close_ts = y['meta'].get('wallTs') or y['ts']
        elif y['side'] in ('long', 'short') and last_close_ts:
            w = y['meta'].get('wallTs') or y['ts']
            if w and last_close_ts and w > last_close_ts:
                gaps.append(w - last_close_ts)
            last_close_ts = None
if gaps:
    gaps.sort()
    print('  reopen gaps sec: n=%d med=%.0f p10=%.0f p90=%.0f  <=900s share=%.0f%%' % (
        len(gaps), st.median(gaps), gaps[int(.1*(len(gaps)-1))], gaps[int(.9*(len(gaps)-1))],
        100.0*sum(1 for g in gaps if g <= 900)/len(gaps)))

print('\n== all 256 closes: net vs fee ==')
closes = [x for x in recs if abs(x['pnl']) > 1e-12]
print('  n=%d sum_pnl=%.2f sum_fee_all_legs=%.2f' % (len(closes), sum(x['pnl'] for x in closes), sum(x['fee'] for x in recs)))
w = [x for x in closes if x['pnl'] > 0]; l = [x for x in closes if x['pnl'] < 0]
print('  wins=%d avg=%.4f   losses=%d avg=%.4f   profit_factor=%.3f' % (
    len(w), st.mean([x['pnl'] for x in w]), len(l), st.mean([x['pnl'] for x in l]),
    sum(x['pnl'] for x in w) / abs(sum(x['pnl'] for x in l))))

print('\n== decisionConfidence vs outcome ==')
byc = defaultdict(list)
for x in recs:
    dc = x['meta'].get('decisionConfidence')
    if dc is None or x['meta'].get('realizedNetBps') is None: continue
    byc[round(dc, 1)].append(x['meta']['realizedNetBps'])
for k in sorted(byc):
    v = byc[k]
    print('  conf=%.1f n=%-3d meanNetBps=%8.1f win%%=%5.1f' % (k, len(v), st.mean(v), 100.0*sum(1 for z in v if z > 0)/len(v)))

print('\n== tripSeq (how many decision rounds before exit) ==')
bt = defaultdict(list)
for x in recs:
    ts_ = x['meta'].get('tripSeq')
    if ts_ is None or x['meta'].get('realizedNetBps') is None: continue
    bt[min(ts_, 6)].append(x['meta']['realizedNetBps'])
for k in sorted(bt):
    v = bt[k]
    print('  tripSeq=%s n=%-3d meanNetBps=%8.1f' % (k, len(v), st.mean(v)))

print('\n== exit reason text buckets (close legs) ==')
print(Counter(x['reason'][:24] for x in closes).most_common(12))

print('\n== stop/tp presence on open legs ==')
opens = [x for x in recs if x['side'] in ('long', 'short')]
print('  opens=%d with_sl=%d with_tp=%d' % (len(opens), sum(1 for x in opens if x['sl']), sum(1 for x in opens if x['tp'])))
print('  notional stats:')
nv = [x['px'] * x['qty'] for x in opens if x['px'] and x['qty']]
if nv:
    nv.sort(); print('   n=%d med=%.1f p10=%.1f p90=%.1f' % (len(nv), st.median(nv), nv[int(.1*(len(nv)-1))], nv[int(.9*(len(nv)-1))]))
