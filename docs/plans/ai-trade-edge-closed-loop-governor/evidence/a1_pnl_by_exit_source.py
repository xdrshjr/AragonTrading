import sqlite3, os, json, statistics as st
from collections import Counter, defaultdict

p = os.path.expandvars(r'%APPDATA%/aragon-desktop/aragon.db')
c = sqlite3.connect('file:' + p + '?mode=ro', uri=True)
q = '''select t.id,t.run_id,t.ts,t.side,t.price,t.qty,t.fee,t.pnl,t.position_after,t.explain_json,r.inst_id,r.bar,r.group_id
from trades t join strategy_runs r on r.id=t.run_id where r.mode="ai-trade" order by t.id'''
rows = c.execute(q).fetchall()

recs = []
for r in rows:
    ej = json.loads(r[9]) if r[9] else {}
    m = ej.get('meta') or {}
    recs.append(dict(id=r[0], run=r[1], ts=r[2], side=r[3], px=r[4], qty=r[5], fee=r[6],
                     pnl=r[7], pos=r[8], inst=r[10], bar=r[11], grp=r[12],
                     kind=ej.get('kind'), meta=m, head=(ej.get('headline') or '')[:80]))

closes = [x for x in recs if x['meta'].get('realizedNetBps') is not None]
opens = [x for x in recs if x['meta'].get('realizedNetBps') is None]
print('legs=%d closes_with_meta=%d others=%d' % (len(recs), len(closes), len(opens)))

def stats(name, vals):
    if not vals:
        print('  %-22s n=0' % name); return
    vals = sorted(vals)
    print('  %-22s n=%-4d sum=%9.2f mean=%8.2f med=%8.2f p10=%8.2f p90=%8.2f' % (
        name, len(vals), sum(vals), st.mean(vals), st.median(vals),
        vals[int(.1*(len(vals)-1))], vals[int(.9*(len(vals)-1))]))

print('\n== realizedNetBps overall ==')
stats('netBps', [x['meta']['realizedNetBps'] for x in closes])
stats('grossBps', [x['meta'].get('realizedGrossBps') for x in closes if x['meta'].get('realizedGrossBps') is not None])
stats('costBps', [x['meta'].get('realizedCostBps') for x in closes if x['meta'].get('realizedCostBps') is not None])

print('\n== by exitSource ==')
by = defaultdict(list)
for x in closes:
    by[x['meta'].get('exitSource', '?')].append(x)
for k, v in sorted(by.items(), key=lambda kv: -len(kv[1])):
    net = [y['meta']['realizedNetBps'] for y in v]
    gross = [y['meta'].get('realizedGrossBps', 0) for y in v]
    usd = sum(y['pnl'] for y in v)
    wins = sum(1 for n in net if n > 0)
    print('  %-16s n=%-4d win%%=%5.1f netBps_sum=%9.1f netBps_mean=%7.1f grossBps_mean=%7.1f pnlUSD=%8.2f' % (
        k, len(v), 100.0*wins/len(v), sum(net), st.mean(net), st.mean(gross), usd))

print('\n== by kind ==')
by = defaultdict(list)
for x in closes:
    by[x['kind']].append(x)
for k, v in sorted(by.items(), key=lambda kv: -len(kv[1])):
    net = [y['meta']['realizedNetBps'] for y in v]
    print('  %-18s n=%-4d netBps_sum=%9.1f mean=%7.1f pnlUSD=%8.2f' % (k, len(v), sum(net), st.mean(net), sum(y['pnl'] for y in v)))

print('\n== holdSeconds ==')
hs = [x['meta'].get('holdSeconds') for x in closes if x['meta'].get('holdSeconds') is not None]
stats('holdSeconds', hs)
buckets = [(0, 300), (300, 900), (900, 1800), (1800, 3600), (3600, 7200), (7200, 21600), (21600, 10**9)]
for lo, hi in buckets:
    sel = [x for x in closes if lo <= (x['meta'].get('holdSeconds') or -1) < hi]
    if not sel: continue
    net = [y['meta']['realizedNetBps'] for y in sel]
    wins = sum(1 for n in net if n > 0)
    print('  hold[%6d,%8d) n=%-4d win%%=%5.1f netBps_mean=%8.1f sum=%9.1f usd=%8.2f' % (
        lo, hi, len(sel), 100.0*wins/len(sel), st.mean(net), sum(net), sum(y['pnl'] for y in sel)))

print('\n== est vs realized edge ==')
pairs = [(x['meta'].get('estEdgeBps'), x['meta'].get('realizedGrossBps'), x['meta'].get('estCostBps'), x['meta'].get('realizedCostBps'))
         for x in closes if x['meta'].get('estEdgeBps') is not None]
print('  pairs n=', len(pairs))
if pairs:
    stats('estEdgeBps', [a for a, b, cc, d in pairs])
    stats('realizedGrossBps', [b for a, b, cc, d in pairs])
    stats('estCostBps', [cc for a, b, cc, d in pairs if cc is not None])
    stats('realizedCostBps', [d for a, b, cc, d in pairs if d is not None])

print('\n== rMultiple / plannedRr ==')
stats('rMultiple', [x['meta']['rMultiple'] for x in closes if x['meta'].get('rMultiple') is not None])
stats('plannedRr', [x['meta']['plannedRr'] for x in closes if x['meta'].get('plannedRr') is not None])

print('\n== by inst ==')
by = defaultdict(list)
for x in closes: by[x['inst']].append(x)
for k, v in sorted(by.items(), key=lambda kv: -len(kv[1])):
    net = [y['meta']['realizedNetBps'] for y in v]
    wins = sum(1 for n in net if n > 0)
    print('  %-18s n=%-4d win%%=%5.1f netBps_mean=%8.1f usd=%8.2f' % (k, len(v), 100.0*wins/len(v), st.mean(net), sum(y['pnl'] for y in v)))

print('\n== by wakeKind (close legs) ==')
by = defaultdict(list)
for x in closes: by[x['meta'].get('wakeKind', '?')].append(x)
for k, v in sorted(by.items(), key=lambda kv: -len(kv[1])):
    net = [y['meta']['realizedNetBps'] for y in v]
    print('  %-18s n=%-4d netBps_mean=%8.1f usd=%8.2f' % (k, len(v), st.mean(net), sum(y['pnl'] for y in v)))

print('\n== ordType on open legs ==')
print(Counter(x['meta'].get('ordType') for x in recs if x['side'] in ('long', 'short')))
print('feeBps distribution:', Counter(x['meta'].get('feeBps') for x in recs))
