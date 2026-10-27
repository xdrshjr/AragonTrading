import sqlite3, os, json, statistics as st
from collections import Counter, defaultdict

p = os.path.expandvars(r'%APPDATA%/aragon-desktop/aragon.db')
c = sqlite3.connect('file:' + p + '?mode=ro', uri=True)
q = '''select t.id,t.run_id,t.ts,t.side,t.price,t.qty,t.fee,t.pnl,t.explain_json,r.inst_id,r.bar,r.group_id
from trades t join strategy_runs r on r.id=t.run_id where r.mode="ai-trade" order by t.id'''
rows = c.execute(q).fetchall()
recs = []
for r in rows:
    ej = json.loads(r[8]) if r[8] else {}
    m = ej.get('meta') or {}
    recs.append(dict(id=r[0], run=r[1], side=r[3], px=r[4], qty=r[5], fee=r[6], pnl=r[7],
                     inst=r[9], bar=r[10], grp=r[11], kind=ej.get('kind'), meta=m,
                     head=(ej.get('headline') or '')))
closes = [x for x in recs if x['meta'].get('realizedNetBps') is not None]

print('== agent exits: how far from the planned stop were they cut? ==')
ag = [x for x in closes if x['meta'].get('exitSource') in ('agent', 'agentOverride')]
rm = sorted(x['meta']['rMultiple'] for x in ag if x['meta'].get('rMultiple') is not None)
print('  n=%d rMultiple med=%.2f p10=%.2f p25=%.2f p75=%.2f p90=%.2f' % (
    len(rm), st.median(rm), rm[int(.1*(len(rm)-1))], rm[int(.25*(len(rm)-1))],
    rm[int(.75*(len(rm)-1))], rm[int(.9*(len(rm)-1))]))
cut_early = [x for x in ag if -1.0 < (x['meta'].get('rMultiple') or 0) < 0]
print('  losers cut BEFORE stop (-1<R<0): n=%d  meanR=%.2f  sum_usd=%.2f' % (
    len(cut_early), st.mean([x['meta']['rMultiple'] for x in cut_early]) if cut_early else 0,
    sum(x['pnl'] for x in cut_early)))
win_small = [x for x in ag if 0 < (x['meta'].get('rMultiple') or 0) < 1.0]
print('  winners cut BEFORE target (0<R<1): n=%d meanR=%.2f sum_usd=%.2f' % (
    len(win_small), st.mean([x['meta']['rMultiple'] for x in win_small]) if win_small else 0,
    sum(x['pnl'] for x in win_small)))
full = [x for x in ag if (x['meta'].get('rMultiple') or 0) >= 1.0]
print('  reached >=1R: n=%d' % len(full))

print('\n== cost as share of |gross| per exit source ==')
by = defaultdict(list)
for x in closes: by[x['meta'].get('exitSource', '?')].append(x)
for k, v in sorted(by.items(), key=lambda kv: -len(kv[1])):
    g = sum(abs(y['meta'].get('realizedGrossBps') or 0) for y in v)
    cst = sum(y['meta'].get('realizedCostBps') or 0 for y in v)
    print('  %-14s n=%-3d sum|gross|=%8.1f sum_cost=%8.1f cost/|gross|=%5.1f%%' % (k, len(v), g, cst, 100*cst/g if g else 0))

print('\n== how many closes had a positive gross but negative net (fee flipped the sign) ==')
flip = [x for x in closes if (x['meta'].get('realizedGrossBps') or 0) > 0 >= (x['meta'].get('realizedNetBps') or 0)]
print('  n=%d of %d (%.0f%%)' % (len(flip), len(closes), 100*len(flip)/len(closes)))

print('\n== turnover: legs per day per session (recent sessions) ==')
by = defaultdict(list)
for x in recs: by[x['grp']].append(x)
for g, v in list(by.items())[-6:]:
    ws = [y['meta'].get('wallTs') for y in v if y['meta'].get('wallTs')]
    if len(ws) < 2: continue
    span_h = (max(ws) - min(ws)) / 3600.0
    print('  %-46s legs=%-3d span=%6.1fh legs/day=%6.1f fee=%6.2f pnl=%7.2f' % (
        (g or '')[:46], len(v), span_h, len(v)/span_h*24 if span_h else 0,
        sum(y['fee'] for y in v), sum(y['pnl'] for y in v)))

print('\n== ordType vs realized (open leg fee) ==')
print('  fee sum by ordType on ALL legs:')
bt = defaultdict(lambda: [0, 0.0, 0.0])
for x in recs:
    o = x['meta'].get('ordType')
    bt[o][0] += 1; bt[o][1] += x['fee']; bt[o][2] += x['px']*x['qty'] if x['px'] and x['qty'] else 0
for k, v in bt.items():
    print('   %-8s n=%-4d fee=%8.3f notional=%12.1f  eff_bps=%6.2f' % (k, v[0], v[1], v[2], 1e4*v[1]/v[2] if v[2] else 0))

print('\n== headline length (prompt/UI) of agent close decisions ==')
hl = [len(x['head']) for x in ag]
if hl: print('  n=%d mean=%.0f max=%d' % (len(hl), st.mean(hl), max(hl)))
