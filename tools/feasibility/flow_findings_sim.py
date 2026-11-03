"""경기 흐름·피드백 요소 발견 가능성 검증(합성 사건열).

검출 모델의 출력(공격·방어·접촉·상태)을 흉내 낸 합성 사건열을 만들고, 02B의 R1~R8을 규칙 기반으로 구현해
심어 둔 패턴을 얼마나 되찾는지 측정한다. 실제 영상·팀원 모델은 쓰지 않는다.
의존성 없음(표준 라이브러리). 실행: python tools/feasibility/flow_findings_sim.py
"""
from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass, field

# ---------------- 임시 매개변수(02B 3.1·3.2의 시작값, team-provisional-v1) ----------------
P = dict(
    delta_link=200,        # ms, 공격–방어 연결 허용 간격
    delta_counter=600,     # ms, 방어 후 반격 허용 간격
    eps_before=200,        # ms, 행동 전 상태 기준
    guard_late=400,        # ms, 가드 복귀 '늦음'
    guard_window=1500,     # ms, 복귀를 기다리는 최대 시간
    k_min=4,               # 반복 표현 최소 판정 가능 횟수
    ratio_min=0.6,         # 문제 발생 비율
    big_balance=1000,      # ms, '큰 밸런스 붕괴' 지속
)
DIST = ['CLINCH', 'CLOSE', 'PUNCH', 'KICK', 'OUTSIDE']
REACH = {'jab': 2, 'cross': 2, 'hook': 2, 'kick': 3}   # 이 인덱스 이하여야 도달
KINDS = ['jab', 'cross', 'hook', 'kick']


# ---------------- 합성 경기 생성(정답 세계) ----------------
@dataclass
class World:
    dist: list = field(default_factory=list)       # (s, e, state)
    attacks: list = field(default_factory=list)    # dict
    defs: list = field(default_factory=list)       # dict
    guard: dict = field(default_factory=dict)      # fighter -> [(s, e, state)]
    balance: dict = field(default_factory=dict)    # fighter -> [(s, e, state)]
    truth: dict = field(default_factory=dict)


def dist_at(w, t):
    for s, e, st in w.dist:
        if s <= t < e:
            return st
    return DIST[2]


def gen_world(rng, planted, T=180_000):
    w = World()
    # 거리 상태 사슬
    t, idx = 0, 2
    while t < T:
        dwell = max(400, int(rng.expovariate(1 / 2000)))
        w.dist.append((t, min(T, t + dwell), DIST[idx]))
        t += dwell
        idx = min(4, max(0, idx + rng.choice([-1, -1, 0, 1, 1])))
    # 공격 생성
    aid = 0
    for f, mean in (('A', 2500), ('B', 2800)):
        t = rng.uniform(0, 1500)
        while t < T - 3000:
            kind = rng.choices(KINDS, [0.37, 0.33, 0.25, 0.05] if (planted and f == 'B') else [0.35, 0.3, 0.15, 0.2])[0]
            state = DIST.index(dist_at(w, t))
            far = state > REACH[kind]
            accept = 0.12
            if planted and f == 'B' and kind == 'kick' and far:
                accept = 0.75           # P2: B의 킥은 도달 밖에서 자주 시도
            if far and rng.random() > accept:
                t += rng.expovariate(1 / mean)
                continue
            dur = rng.gauss(400, 40)
            a = dict(id=aid, actor=f, target='B' if f == 'A' else 'A', kind=kind, s=t, p=t + dur * 0.5, e=t + dur,
                     far=far, counter=False)
            aid += 1
            w.attacks.append(a)
            t += rng.expovariate(1 / mean)
    if planted:   # P2: B는 도달 밖(OUTSIDE)에서 킥을 자주 시도
        for s, e, st in w.dist:
            if st != 'OUTSIDE':
                continue
            t0 = s
            while t0 < e - 400:
                if rng.random() < 0.8:
                    w.attacks.append(dict(id=aid, actor='B', target='A', kind='kick', s=t0, p=t0 + 200, e=t0 + 400, far=True, counter=False))
                    aid += 1
                t0 += 1500
    # 방어·접촉 결과, 반격
    new = []
    for a in w.attacks:
        d = {'actor': a['target']}
        if a['far']:
            a['result'], a['region'] = 'MISS', None
            if rng.random() < 0.1:
                w.defs.append(dict(actor=a['target'], kind='step', s=a['s'] + 100, e=a['e'], atk=a['id']))
            continue
        if rng.random() < 0.5:
            kind = rng.choices(['block', 'slip', 'step'], [0.5, 0.3, 0.2])[0]
            ds = a['s'] + rng.uniform(80, 200)
            w.defs.append(dict(actor=a['target'], kind=kind, s=ds, e=a['e'] + 100, atk=a['id']))
            if kind == 'block':
                a['result'], a['region'] = 'BLOCKED', 'guard'
            else:
                a['result'], a['region'] = 'EVADED', None
                if rng.random() < 0.4:
                    cs = ds + rng.uniform(250, 450)
                    new.append(dict(id=-1, actor=a['target'], target=a['actor'], kind='cross', s=cs, p=cs + 200,
                                    e=cs + 400, far=False, counter=True, result='MISS', region=None))
        else:
            a['result'] = 'HIT'
            a['region'] = rng.choices(['head', 'body', 'leg'], [0.5, 0.4, 0.1])[0]
    for n in new:
        n['id'] = len(w.attacks)
        w.attacks.append(n)
    w.attacks.sort(key=lambda x: x['s'])
    for i, a in enumerate(w.attacks):
        a['id'] = i
    # 가드·밸런스 상태
    for f in ('A', 'B'):
        down = []
        for a in w.attacks:
            if a['actor'] != f:
                continue
            delay = max(80, rng.gauss(250, 80))
            if planted and f == 'A' and a['kind'] == 'cross' and rng.random() < 0.7:
                delay = max(500, rng.gauss(850, 150))   # P1: A의 크로스 뒤 가드 복귀 지연
            down.append([a['s'] + 100, a['e'] + delay])
            a['delay'] = delay
        w.guard[f] = _intervals(down, T, 'DOWN', 'HIGH')
        unst = []
        for a in w.attacks:
            if a['target'] == f and a.get('result') == 'HIT' and rng.random() < 0.1:
                unst.append([a['p'], a['p'] + rng.uniform(400, 900)])
            if a['actor'] == f and rng.random() < 0.05:
                unst.append([a['e'], a['e'] + rng.uniform(400, 900)])
        w.balance[f] = unst
    big = []
    if planted:
        for _ in range(3):
            f = rng.choice(['A', 'B'])
            s = rng.uniform(10_000, T - 10_000)
            d = rng.uniform(1500, 2500)
            w.balance[f].append([s, s + d])
            big.append((f, s, s + d))
    for f in ('A', 'B'):
        w.balance[f] = _merge(w.balance[f])
    w.truth = dict(big=[(f, s, e) for f in ('A', 'B') for s, e in w.balance[f] if e - s >= P['big_balance']])
    return w


def _merge(iv):
    iv = sorted(iv)
    out = []
    for s, e in iv:
        if out and s <= out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return out


def _intervals(down, T, on, off):
    out, t = [], 0
    for s, e in _merge(down):
        if s > t:
            out.append((t, s, off))
        out.append((s, e, on))
        t = e
    if t < T:
        out.append((t, T, off))
    return out


# ---------------- 관측 모델(검출 모델 출력 흉내) ----------------
NOISE = {
    'low':  dict(a_miss=0.05, a_fp=0.5, jitter=40, d_miss=0.05, c_unknown=0.10, c_err=0.03, dist_err=0.05, g_unknown=0.10, b_miss=0.05),
    'mid':  dict(a_miss=0.15, a_fp=1.5, jitter=120, d_miss=0.15, c_unknown=0.25, c_err=0.10, dist_err=0.15, g_unknown=0.30, b_miss=0.15),
    'high': dict(a_miss=0.30, a_fp=3.0, jitter=240, d_miss=0.30, c_unknown=0.40, c_err=0.20, dist_err=0.30, g_unknown=0.50, b_miss=0.30),
}


def observe(rng, w, n, T=180_000):
    obs = dict(atk=[], dfs=[], con={}, dist=[], guard={}, bal={})
    for a in w.attacks:
        if rng.random() < n['a_miss']:
            continue
        j = lambda: rng.gauss(0, n['jitter'])
        o = dict(id=a['id'], actor=a['actor'], target=a['target'], kind=a['kind'], s=a['s'] + j(), p=a['p'] + j(), e=a['e'] + j())
        obs['atk'].append(o)
        if rng.random() < n['c_unknown']:
            obs['con'][a['id']] = ('UNKNOWN', None)
        else:
            reg = a.get('region')
            if reg in ('guard', 'body') and rng.random() < n['c_err']:
                reg = 'body' if reg == 'guard' else 'guard'
            obs['con'][a['id']] = ('VALID', reg)
    nf = int(n['a_fp'] * T / 60_000)
    for k in range(nf):
        s = rng.uniform(0, T - 1000)
        f = rng.choice(['A', 'B'])
        obs['atk'].append(dict(id=10_000 + k, actor=f, target='B' if f == 'A' else 'A', kind=rng.choice(KINDS), s=s, p=s + 200, e=s + 400))
        obs['con'][10_000 + k] = ('UNKNOWN', None)
    for d in w.defs:
        if rng.random() < n['d_miss']:
            continue
        obs['dfs'].append(dict(actor=d['actor'], kind=d['kind'], s=d['s'] + rng.gauss(0, n['jitter']), e=d['e'] + rng.gauss(0, n['jitter'])))
    for s, e, st in w.dist:
        if rng.random() < n['dist_err']:
            i = DIST.index(st)
            st = DIST[min(4, max(0, i + rng.choice([-1, 1])))]
        obs['dist'].append((s, e, st))
    for f in ('A', 'B'):
        g = [(s, e, st) for s, e, st in w.guard[f]]
        t = 0
        unk = []
        gmean = 2000 * (1 - n['g_unknown']) / n['g_unknown'] if n['g_unknown'] > 0 else 1e12
        while t < T:
            t += rng.expovariate(1 / gmean) if gmean < 1e11 else T
            ln = rng.uniform(1000, 3000)
            unk.append((t, t + ln))
            t += ln
        obs['guard'][f] = (g, unk)
        obs['bal'][f] = [(s + rng.gauss(0, n['jitter']), e + rng.gauss(0, n['jitter'])) for s, e in w.balance[f] if rng.random() >= n['b_miss']]
    return obs


# ---------------- 분석(02B R1~R8) ----------------
def guard_at(obs, f, t):
    g, unk = obs['guard'][f]
    for s, e in unk:
        if s <= t < e:
            return 'UNKNOWN'
    for s, e, st in g:
        if s <= t < e:
            return st
    return 'UNKNOWN'


def dist_state(obs, t):
    for s, e, st in obs['dist']:
        if s <= t < e:
            return st
    return 'UNKNOWN'


def analyze(obs):
    atk, dfs = obs['atk'], obs['dfs']
    rows = []
    for a in atk:
        # R1 방어 연결
        cand = [d for d in dfs if d['actor'] == a['target'] and d['s'] <= a['e'] + P['delta_link'] and d['e'] >= a['s'] - P['delta_link']]
        status, region = obs['con'][a['id']]
        link = 'NONE' if not cand else cand[0]['kind']
        dsel = cand[0] if cand else None
        # R2 결과
        if status == 'UNKNOWN':
            result = 'UNKNOWN'
        elif region == 'guard' and link in ('block',):
            result = 'BLOCKED'
        elif region in ('head', 'body', 'leg') and link == 'NONE':
            result = 'HIT'
        elif region is None and link in ('slip', 'step'):
            result = 'EVADED'
        elif region is None and link == 'NONE':
            result = 'MISS'
        else:
            result = 'UNKNOWN'
        # R4 거리
        st = dist_state(obs, a['s'] - P['eps_before'])
        far = None if st == 'UNKNOWN' else DIST.index(st) > REACH[a['kind']]
        # R5 가드 복귀
        delay = None
        unknown = False
        t = a['e']
        for dt in range(0, P['guard_window'] + 1, 40):
            gs = guard_at(obs, a['actor'], t + dt)
            if gs == 'UNKNOWN':
                unknown = True
                break
            if gs == 'HIGH':
                delay = dt
                break
        if unknown:
            delay = None
        elif delay is None:
            delay = P['guard_window']
        rows.append(dict(a, link=link, dsel=dsel, result=result, far=far, delay=delay))
    # R3 반격: 방어·회피 뒤 방어자의 공격이 상대 최대 동작 이후~방어 종료+간격 안에 시작(동시 공격은 교환)
    counters = set()
    for r in rows:
        if r['dsel'] is not None:
            for r2 in rows:
                if r2['actor'] == r['target'] and r2['s'] >= r['p'] and r2['s'] <= r['dsel']['e'] + P['delta_counter']:
                    counters.add(r2['id'])
    # R7 반복 집계 + 발견
    findings = []
    keys = {(r['actor'], r['kind']) for r in rows}
    for k in keys:
        grp = [r for r in rows if (r['actor'], r['kind']) == k]
        judged = [r for r in grp if r['delay'] is not None]
        late = [r for r in judged if r['delay'] > P['guard_late']]
        if len(judged) >= P['k_min'] and len(late) / len(judged) >= P['ratio_min']:
            findings.append(dict(type='GUARD_LATE', key=k, n_all=len(grp), n_judged=len(judged), n_event=len(late), n_unknown=len(grp) - len(judged),
                                 evidence=[r['id'] for r in late]))
        jr = [r for r in grp if r['far'] is not None]
        far = [r for r in jr if r['far']]
        if len(far) >= P['k_min'] and len(far) / len(jr) >= 0.5:
            findings.append(dict(type='RANGE_FAIL', key=k, n_all=len(grp), n_judged=len(jr), n_event=len(far), n_unknown=len(grp) - len(jr),
                                 evidence=[r['id'] for r in far]))
    # R8 중요 장면
    scenes = []
    for f in ('A', 'B'):
        for s, e in obs['bal'][f]:
            if e - s >= P['big_balance']:
                scenes.append((f, s, e))
    return dict(rows=rows, findings=findings, scenes=scenes, counters=counters)


# ---------------- 평가 ----------------
def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - m) / d, (c + m) / d)


def evaluate(w, res, planted, obs):
    out = {}
    f = res['findings']
    out['p1'] = planted and any(x['type'] == 'GUARD_LATE' and x['key'] == ('A', 'cross') for x in f)
    out['p2'] = planted and any(x['type'] == 'RANGE_FAIL' and x['key'] == ('B', 'kick') for x in f)
    out['fp_guard'] = sum(1 for x in f if x['type'] == 'GUARD_LATE' and not (planted and x['key'] == ('A', 'cross')))
    out['fp_range'] = sum(1 for x in f if x['type'] == 'RANGE_FAIL' and not (planted and x['key'] == ('B', 'kick')))
    tc = {a['id'] for a in w.attacks if a['counter']}
    seen = {o['id'] for o in obs['atk']}
    det = res['counters']
    out['c_tp'] = len(det & tc & seen)
    out['c_true'] = len(tc & seen)
    out['c_det'] = len(det)
    out['c_det_true'] = len(det & tc)
    big = w.truth['big']
    hit = 0
    for fighter, s, e in big:
        if any(sf == fighter and ss < e and se > s for sf, ss, se in res['scenes']):
            hit += 1
    out['big_hit'], out['big_n'] = hit, len(big)
    matched = 0
    for sf, ss, se in res['scenes']:
        if any(fighter == sf and ss < e and se > s for fighter, s, e in big):
            matched += 1
    out['scene_tp'], out['scene_n'] = matched, len(res['scenes'])
    # 불변식: 분모 + 판단 불가 = 전체, 모든 발견에 근거 연결
    inv = all(x['n_judged'] + x['n_unknown'] == x['n_all'] and len(x['evidence']) == x['n_event'] for x in f)
    out['invariant'] = inv
    return out


def run(n_bouts, level, planted, seed, T=180_000):
    rng = random.Random(seed)
    agg = dict(p1=0, p2=0, big_hit=0, big_n=0, scene_tp=0, scene_n=0, fp_guard=0, fp_range=0, inv=0, bouts=n_bouts, t=0.0, c_tp=0, c_true=0, c_det=0, c_det_true=0)
    for _ in range(n_bouts):
        w = gen_world(rng, planted, T)
        o = observe(rng, w, NOISE[level] if isinstance(level, str) else level, T)
        t0 = time.perf_counter()
        r = analyze(o)
        agg['t'] += time.perf_counter() - t0
        e = evaluate(w, r, planted, o)
        for k in ('p1', 'p2', 'big_hit', 'big_n', 'scene_tp', 'scene_n', 'fp_guard', 'fp_range', 'c_tp', 'c_true', 'c_det', 'c_det_true'):
            agg[k] += int(e[k])
        agg['inv'] += int(e['invariant'])
    return agg


def fmt(k, n):
    lo, hi = wilson(k, n)
    return f'{k}/{n} = {k / n:.2f} (95% {lo:.2f}~{hi:.2f})' if n else '-'


def sweep_line(label, a, N):
    return f'  {label}  P1 {fmt(a["p1"], N)}  P2 {fmt(a["p2"], N)}'


def main():
    N = 200
    print('매개변수', P)
    print(f'\n[E1·E2] 잡음 수준별 결과. 경기 {N}개씩(한 경기 180초), 심은 패턴 있음 / 없음(null)')
    for level in ('low', 'mid', 'high'):
        pl = run(N, level, True, seed=1)
        nu = run(N, level, False, seed=2)
        print(f'[잡음 {level}]')
        print(f'  P1 가드 복귀 지연(A·크로스) 재현   {fmt(pl["p1"], N)}')
        print(f'  P2 거리 실패(B·킥) 재현            {fmt(pl["p2"], N)}')
        print(f'  P3 큰 밸런스 붕괴 장면 재현        {fmt(pl["big_hit"], pl["big_n"])}')
        print(f'  중요 장면 정밀도                   {fmt(pl["scene_tp"], pl["scene_n"])}')
        print(f'  반격 재현율 / 정밀도               {fmt(pl["c_tp"], pl["c_true"])} / {fmt(pl["c_det_true"], pl["c_det"])}')
        print(f'  null 경기 오발견(경기당)           가드 {nu["fp_guard"] / N:.3f}, 거리 {nu["fp_range"] / N:.3f}  (패턴 없는 {N}경기)')
        print(f'  분모 불변식 유지율 {pl["inv"] / N:.2f}, 처리 {1000 * pl["t"] / N:.1f}ms/경기')
    print('\n[E3] 공격 검출 누락률에 따른 재현율(그 외 잡음 mid)')
    for m in (0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6):
        print(sweep_line(f'a_miss={m:.1f}', run(N, dict(NOISE['mid'], a_miss=m), True, seed=3), N))
    print('\n[E4] 가드 상태 판단 불가 시간 비율에 따른 P1 재현율(그 외 잡음 mid)')
    for g in (0.0, 0.2, 0.4, 0.6, 0.8):
        a = run(N, dict(NOISE['mid'], g_unknown=g), True, seed=4)
        print(f'  g_unknown={g:.1f}  P1 {fmt(a["p1"], N)}')
    print('\n[E5] 반복 표현 기준(k_min × ratio_min) 민감도, 잡음 mid')
    print('  k_min ratio  P1 재현율   P2 재현율   null 오발견/경기')
    base = dict(P)
    for k in (3, 4, 5, 6):
        for r in (0.5, 0.6, 0.7):
            P['k_min'], P['ratio_min'] = k, r
            pl = run(N, 'mid', True, seed=5)
            nu = run(N, 'mid', False, seed=6)
            print(f'  {k:<5} {r:<5} {pl["p1"] / N:<11.2f} {pl["p2"] / N:<11.2f} {(nu["fp_guard"] + nu["fp_range"]) / N:.3f}')
    P.update(base)
    print('\n[E6] 관측 길이에 따른 결과, 잡음 mid, k_min=5·ratio_min=0.6')
    P['k_min'], P['ratio_min'] = 5, 0.6
    for sec in (180, 360, 540, 900):
        pl = run(N, 'mid', True, seed=7, T=sec * 1000)
        nu = run(N, 'mid', False, seed=8, T=sec * 1000)
        print(f'  {sec}초  P1 {pl["p1"] / N:.2f}  P2 {pl["p2"] / N:.2f}  null 오발견/경기 {(nu["fp_guard"] + nu["fp_range"]) / N:.3f}')
    P.update(base)


if __name__ == '__main__':
    main()
