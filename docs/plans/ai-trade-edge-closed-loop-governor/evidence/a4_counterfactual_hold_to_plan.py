"""Counterfactual: had each AI-opened position been held to its own OCO stop/target,
what would the R outcome have been vs what the agent actually did?"""
import sqlite3, os, json, statistics as st
from collections import defaultdict

p = os.path.expandvars(r'%APPDATA%/aragon-desktop/aragon.db')
c = sqlite3.connect('file:' + p + '?mode=ro', uri=True)

q = '''select t.id,t.run_id,t.ts,t.side,t.price,t.qty,t.pnl,t.explain_json,
t.stop_loss_price,t.take_profit_price,r.inst_id,r.bar
from trades t join strategy_runs r on r.id=t.run_id where r.mode="ai-trade" order by t.id'''
rows = c.execute(q).fetchall()
legs = []
for r in rows:
    ej = json.loads(r[7]) if r[7] else {}
    legs.append(dict(id=r[0], run=r[1], ts=r[2], side=r[3], px=r[4], qty=r[5], pnl=r[6],
                     sl=r[8], tp=r[9], inst=r[10], bar=r[11],
                     kind=ej.get('kind'), meta=(ej.get('meta') or {})))

# pair open -> close per run, in id order
pairs = []
byrun = defaultdict(list)
for x in legs: byrun[x['run']].append(x)
for run, v in byrun.items():
    v.sort(key=lambda y: y['id'])
    cur = None
    for y in v:
        if y['side'] in ('long', 'short'):
            cur = y
        elif cur is not None:
            pairs.append((cur, y)); cur = None

print('paired round trips:', len(pairs))
sel = [(o, cl) for o, cl in pairs
       if cl['meta'].get('exitSource') in ('agent', 'agentOverride')
       and o['sl'] and o['tp'] and o['px']]
print('agent-exit round trips with stop+target recorded:', len(sel))

def candles(inst, bar, ts_from, ts_to):
    return c.execute('select ts,h,l,c from candles_cache where inst_id=? and bar=? and ts>=? and ts<=? order by ts',
                     (inst, bar, ts_from, ts_to)).fetchall()

HORIZON_BARS = 96          # look forward at most 96 bars (24h on 15m, 48h on 30m)
BAR_S = {'1m': 60, '5m': 300, '15m': 900, '30m': 1800, '1H': 3600, '4H': 14400}
res = []
missing = 0
for o, cl in sel:
    step = BAR_S.get(o['bar'])
    if not step:
        missing += 1; continue
    t0 = int(o['ts']) + step         # first bar strictly after the entry bar
    t1 = t0 + step * HORIZON_BARS
    cs = candles(o['inst'], o['bar'], t0, t1)
    if len(cs) < 4:
        missing += 1; continue
    entry, sl, tp = float(o['px']), float(o['sl']), float(o['tp'])
    risk = abs(entry - sl)
    if risk <= 0:
        missing += 1; continue
    rr = abs(tp - entry) / risk
    long_ = o['side'] == 'long'
    outcome, bars = None, 0
    for ts, h, l, cc in cs:
        bars += 1
        hit_sl = (l <= sl) if long_ else (h >= sl)
        hit_tp = (h >= tp) if long_ else (l <= tp)
        if hit_sl and hit_tp:
            outcome = -1.0; break          # same-bar ambiguity -> conservative: stop first
        if hit_sl:
            outcome = -1.0; break
        if hit_tp:
            outcome = rr; break
    if outcome is None:
        last = cs[-1][3]
        outcome = ((last - entry) if long_ else (entry - last)) / risk
    actual = cl['meta'].get('rMultiple')
    res.append(dict(inst=o['inst'], bar=o['bar'], side=o['side'], actualR=actual,
                    planR=outcome, rr=rr, bars=bars, pnl=cl['pnl'],
                    src=cl['meta'].get('exitSource')))

print('evaluated:', len(res), ' skipped (no candles/bad inputs):', missing)
if res:
    a = [x['actualR'] for x in res if x['actualR'] is not None]
    pl = [x['planR'] for x in res if x['actualR'] is not None]
    print('  actual  R: n=%d sum=%7.2f mean=%6.3f median=%6.3f  win%%=%.0f' % (
        len(a), sum(a), st.mean(a), st.median(a), 100*sum(1 for v in a if v > 0)/len(a)))
    print('  plan-held R: n=%d sum=%7.2f mean=%6.3f median=%6.3f  win%%=%.0f' % (
        len(pl), sum(pl), st.mean(pl), st.median(pl), 100*sum(1 for v in pl if v > 0)/len(pl)))
    better = sum(1 for x in res if x['actualR'] is not None and x['planR'] > x['actualR'])
    print('  plan-held beat the agent exit on %d / %d (%.0f%%)' % (better, len(a), 100*better/len(a)))
    print('  outcome mix: stop=%d target=%d timeout=%d' % (
        sum(1 for x in res if abs(x['planR'] + 1) < 1e-9),
        sum(1 for x in res if abs(x['planR'] - x['rr']) < 1e-9),
        sum(1 for x in res if abs(x['planR'] + 1) > 1e-9 and abs(x['planR'] - x['rr']) > 1e-9)))
    print()
    for x in res:
        print('   %-16s %-5s %-3s actualR=%7.3f  planHeldR=%7.3f  rr=%.2f bars=%d' % (
            x['inst'], x['bar'], x['side'],
            x['actualR'] if x['actualR'] is not None else float('nan'), x['planR'], x['rr'], x['bars']))
