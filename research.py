"""Dò tìm đặc trưng xuất hiện TRƯỚC khi coin Alpha pump.

Mọi đặc trưng chỉ dùng dữ liệu quá khứ tại thời điểm t, nhãn là mức tăng tối đa
trong H nến kế tiếp. Có kiểm định train/test theo thời gian để loại bớt overfit.

Cách dùng:
    python3 research.py --fetch --days 60            # tải lịch sử (phân trang)
    python3 research.py --pump 10 --horizon 12       # bảng lift từng đặc trưng
    python3 research.py --combo                      # dò điều kiện kết hợp
    python3 research.py --validate                   # train 70% đầu, test 30% sau
"""
import argparse
import time
from collections import deque, namedtuple
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import numpy as np

import store
from alpha_price import get_klines, get_tokens

# cửa sổ tính bằng số nến
W_S, W_M, W_L, W_BASE = 3, 15, 60, 240

INTERVAL_MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000,
               "30m": 1_800_000, "1h": 3_600_000, "2h": 7_200_000,
               "4h": 14_400_000, "6h": 21_600_000, "12h": 43_200_000,
               "1d": 86_400_000}

FEATURES = [
    ("vol_x_ngan", "volume/nến 3 nến gần nhất so với nền"),
    ("vol_x_vua", "volume/nến 15 nến gần nhất so với nền"),
    ("lenh_x", "số lệnh/nến 15 nến gần nhất so với nền"),
    ("size_lenh_x", "giá trị trung bình mỗi lệnh so với nền"),
    ("ret_ngan", "% thay đổi giá 3 nến"),
    ("ret_vua", "% thay đổi giá 15 nến"),
    ("ret_dai", "% thay đổi giá 60 nến"),
    ("vi_tri_range", "vị trí giá trong biên độ 60 nến (0=đáy, 1=đỉnh)"),
    ("cach_dinh", "% cách đỉnh 240 nến"),
    ("ty_le_hoat_dong", "tỷ lệ nến có giao dịch trong 15 nến"),
    ("bien_dong", "độ lệch chuẩn lợi suất 60 nến (%)"),
]
NAMES = [n for n, _ in FEATURES]
IDX = {n: i for i, n in enumerate(NAMES)}

Cond = namedtuple("Cond", "label name op thr")


def mask_of(F: np.ndarray, c: Cond) -> np.ndarray:
    col = F[:, IDX[c.name]]
    return col >= c.thr if c.op == ">=" else col <= c.thr


def rolling_extreme(values, window, use_max):
    """max/min trượt của `window` phần tử kết thúc tại i."""
    out = [0.0] * len(values)
    dq = deque()
    for i, v in enumerate(values):
        while dq and ((values[dq[-1]] <= v) if use_max else (values[dq[-1]] >= v)):
            dq.pop()
        dq.append(i)
        if dq[0] <= i - window:
            dq.popleft()
        out[i] = values[dq[0]]
    return out


def build_samples(bars, pump_pct: float, horizon: int, min_bar_volume: float):
    """Sinh (vector đặc trưng, nhãn pump, gain, thời điểm) cho từng nến đủ lịch sử."""
    n = len(bars)
    if n < W_BASE + horizon + 2:
        return []

    close = [b.close for b in bars]
    qv = [b.quote_volume for b in bars]
    trd = [float(b.trades) for b in bars]

    pq, pt = [0.0], [0.0]
    for i in range(n):
        pq.append(pq[-1] + qv[i])
        pt.append(pt[-1] + trd[i])

    rets = [0.0] + [(close[i] / close[i - 1] - 1) if close[i - 1] else 0.0 for i in range(1, n)]
    pr, pr2, pnz = [0.0], [0.0], [0]
    for i, r in enumerate(rets):
        pr.append(pr[-1] + r)
        pr2.append(pr2[-1] + r * r)
    for i in range(n):
        pnz.append(pnz[-1] + (1 if qv[i] > 0 else 0))

    hi_l = rolling_extreme([b.high for b in bars], W_L, True)
    lo_l = rolling_extreme([b.low for b in bars], W_L, False)
    hi_base = rolling_extreme([b.high for b in bars], W_BASE, True)
    fut_hi = rolling_extreme([b.high for b in bars], horizon, True)
    fut_lo = rolling_extreme([b.low for b in bars], horizon, False)

    def avg(prefix, start, end):
        return (prefix[end] - prefix[start]) / (end - start)

    out = []
    for i in range(W_BASE, n - horizon):
        v_base = avg(pq, i - W_BASE, i - W_M)
        t_base = avg(pt, i - W_BASE, i - W_M)
        v_m = avg(pq, i - W_M + 1, i + 1)
        if v_m < min_bar_volume or v_base <= 0 or t_base <= 0:
            continue
        v_s = avg(pq, i - W_S + 1, i + 1)
        t_m = avg(pt, i - W_M + 1, i + 1)

        size_base = v_base / t_base
        rng = hi_l[i] - lo_l[i]
        m = (pr[i + 1] - pr[i + 1 - W_L]) / W_L
        var = (pr2[i + 1] - pr2[i + 1 - W_L]) / W_L - m * m

        feats = (
            v_s / v_base,
            v_m / v_base,
            t_m / t_base,
            (v_m / t_m) / size_base if t_m > 0 and size_base > 0 else 0.0,
            (close[i] / close[i - W_S] - 1) * 100 if close[i - W_S] else 0.0,
            (close[i] / close[i - W_M] - 1) * 100 if close[i - W_M] else 0.0,
            (close[i] / close[i - W_L] - 1) * 100 if close[i - W_L] else 0.0,
            (close[i] - lo_l[i]) / rng if rng > 0 else 0.5,
            (close[i] / hi_base[i] - 1) * 100 if hi_base[i] else 0.0,
            (pnz[i + 1] - pnz[i + 1 - W_M]) / W_M,
            max(var, 0.0) ** 0.5 * 100,
        )
        gain = (fut_hi[i + horizon] / close[i] - 1) * 100
        mae = (fut_lo[i + horizon] / close[i] - 1) * 100
        out.append((feats, gain >= pump_pct, gain, mae, bars[i].open_time))
    return out


def live_features(bars, min_bar_volume: float = 0.0):
    """Vector đặc trưng tại nến đã đóng cuối cùng, dùng chung code với build_samples."""
    if len(bars) < W_BASE + 3:
        return None
    # thêm 1 nến giả để horizon=1 quét được tới nến thật cuối cùng; nhãn bị bỏ đi
    s = build_samples(bars + [bars[-1]], 1e9, 1, min_bar_volume)
    if not s or s[-1][4] != bars[-1].open_time:
        return None
    return s[-1][0]


def load_dataset(conn, args):
    samples, ids = [], []
    for alpha_id, _, _ in store.list_symbols(conn, args.interval):
        bars = store.load_bars(conn, alpha_id, args.interval)
        s = build_samples(bars, args.pump, args.horizon, args.min_bar_volume)
        samples += s
        ids += [alpha_id] * len(s)
    if not samples:
        return None
    F = np.array([s[0] for s in samples], dtype=np.float64)
    hits = np.array([s[1] for s in samples], dtype=bool)
    gains = np.array([s[2] for s in samples], dtype=np.float64)
    maes = np.array([s[3] for s in samples], dtype=np.float64)
    times = np.array([s[4] for s in samples], dtype=np.int64)
    return F, hits, gains, maes, times, np.array(ids)


def expectancy(gains, maes, sel, tp: float, sl: float):
    """Kỳ vọng mỗi lệnh, giả định thận trọng: chạm SL trước nếu cả hai cùng chạm."""
    g, m = gains[sel], maes[sel]
    hit_sl = m <= -sl
    hit_tp = (g >= tp) & ~hit_sl
    n = len(g)
    if not n:
        return 0.0, 0.0, 0.0
    timeout = ~hit_sl & ~hit_tp
    pnl = (hit_tp.sum() * tp - hit_sl.sum() * sl) / n
    return pnl, hit_tp.mean() * 100, hit_sl.mean() * 100


def lift_table(F, hits, gains, name: str, buckets: int = 5) -> None:
    col = F[:, IDX[name]]
    edges = np.quantile(col, [k / buckets for k in range(1, buckets)])
    base = hits.mean()
    print(f"\n{name} — {dict(FEATURES)[name]}")
    print(f"{'khoảng':>22}{'n':>9}{'pump %':>9}{'lift':>7}{'gain TB %':>11}")
    bounds = [col.min()] + list(edges) + [col.max()]
    for k in range(buckets):
        sel = (col >= bounds[k]) & (col <= bounds[k + 1]) if k == buckets - 1 else (col >= bounds[k]) & (col < bounds[k + 1])
        n = int(sel.sum())
        if not n:
            continue
        rate = hits[sel].mean()
        bar = "*" * min(int(rate / base * 3), 30) if base > 0 else ""
        print(f"{bounds[k]:>10.2f}..{bounds[k+1]:<10.2f}{n:>9}{rate*100:>9.2f}"
              f"{rate/base:>7.2f}{gains[sel].mean():>11.2f}  {bar}")


def build_conditions(F, quantiles=(0.5, 0.7, 0.85, 0.95)):
    conds = []
    for name in NAMES:
        col = F[:, IDX[name]]
        for q in quantiles:
            hi = float(np.quantile(col, q))
            lo = float(np.quantile(col, 1 - q))
            conds.append(Cond(f"{name} >= {hi:.2f}", name, ">=", hi))
            conds.append(Cond(f"{name} <= {lo:.2f}", name, "<=", lo))
    return conds


def rank_combos(F, hits, gains, conds, min_support: int):
    """Chấm mọi cặp điều kiện bằng phép toán vector."""
    masks = np.array([mask_of(F, c) for c in conds])
    base = hits.mean()
    results = []
    for i in range(len(conds)):
        mi = masks[i]
        if np.count_nonzero(mi) < min_support:
            continue
        for j in range(i + 1, len(conds)):
            if conds[i].name == conds[j].name:
                continue
            both = mi & masks[j]
            n = int(np.count_nonzero(both))
            if n < min_support:
                continue
            h = int(np.count_nonzero(both & hits))
            rate = h / n
            results.append((rate / base if base else 0, rate, n, both, conds[i], conds[j]))
    results.sort(key=lambda r: -r[0])
    return results, base


def combo_search(F, hits, gains, maes, min_support: int, tp: float, sl: float, top: int = 15) -> None:
    conds = build_conditions(F)
    results, base = rank_combos(F, hits, gains, conds, min_support)
    print(f"\nTop điều kiện kết hợp (xác suất nền {base*100:.2f}%, tối thiểu {min_support} mẫu)")
    print(f"{'lift':>6}{'pump %':>9}{'n':>8}{'gainTB':>8}{'gainTV':>8}"
          f"{'TP%':>7}{'SL%':>7}{'EV%':>7}  điều kiện")
    for lift, rate, n, both, c1, c2 in results[:top]:
        g = gains[both]
        ev, tpr, slr = expectancy(gains, maes, both, tp, sl)
        print(f"{lift:>6.2f}{rate*100:>9.2f}{n:>8}{g.mean():>8.2f}{np.median(g):>8.2f}"
              f"{tpr:>7.1f}{slr:>7.1f}{ev:>7.2f}  {c1.label}  VÀ  {c2.label}")


def validate(F, hits, gains, maes, times, min_support: int, tp: float, sl: float,
             top: int = 15, split: float = 0.7) -> None:
    cut = np.quantile(times, split)
    tr, te = times < cut, times >= cut
    print(f"\nTrain: {int(tr.sum()):,} mẫu trước {datetime.fromtimestamp(cut/1000):%d/%m %H:%M} | "
          f"Test: {int(te.sum()):,} mẫu sau đó")

    Ftr, Fte = F[tr], F[te]
    htr, hte = hits[tr], hits[te]
    gte, mte = gains[te], maes[te]
    conds = build_conditions(Ftr)
    results, base_tr = rank_combos(Ftr, htr, gains[tr], conds, min_support)
    base_te = hte.mean()
    ev_base, _, _ = expectancy(gte, mte, np.ones(len(gte), dtype=bool), tp, sl)
    print(f"xác suất nền: train {base_tr*100:.2f}% | test {base_te*100:.2f}% | "
          f"EV nền test với TP+{tp}/SL-{sl}: {ev_base:+.2f}%")
    print(f"\n{'lift_tr':>8}{'lift_te':>8}{'pump_te':>9}{'n_te':>7}{'gain_te':>8}"
          f"{'TP%':>7}{'SL%':>7}{'EV%':>7}  điều kiện")
    for lift, _, _, _, c1, c2 in results[:top]:
        sel = mask_of(Fte, c1) & mask_of(Fte, c2)
        n = int(sel.sum())
        if n < 30:
            print(f"{lift:>8.2f}{'-':>8}{'-':>9}{n:>7}{'-':>8}{'-':>7}{'-':>7}{'-':>7}"
                  f"  {c1.label}  VÀ  {c2.label}")
            continue
        rate = hte[sel].mean()
        ev, tpr, slr = expectancy(gte, mte, sel, tp, sl)
        print(f"{lift:>8.2f}{(rate/base_te if base_te else 0):>8.2f}{rate*100:>9.2f}"
              f"{n:>7}{gte[sel].mean():>8.2f}{tpr:>7.1f}{slr:>7.1f}{ev:>7.2f}"
              f"  {c1.label}  VÀ  {c2.label}")


def fetch_history(alpha_id: str, interval: str, days: float, limit: int = 1500) -> list:
    """Lấy nến lùi về `days` ngày bằng cách phân trang theo startTime."""
    step = INTERVAL_MS[interval]
    now_ms = int(time.time() * 1000)
    cursor = now_ms - int(days * 86_400_000)
    out, seen = [], set()
    while cursor < now_ms:
        chunk = []
        for attempt in range(3):
            try:
                chunk = get_klines(alpha_id, interval, limit, start_time=cursor)
                break
            except Exception:
                if attempt == 2:
                    return out
                time.sleep(1 + attempt)
        if not chunk:
            break
        out += [c for c in chunk if int(c[0]) not in seen]
        seen.update(int(c[0]) for c in chunk)
        last = int(chunk[-1][0])
        if last <= cursor or len(chunk) < limit:
            break
        cursor = last + step
    return out


def research_universe(tokens: list, args) -> list:
    """Vũ trụ nghiên cứu rộng hơn watchlist alert để có đủ mẫu pump."""
    return [
        t for t in tokens
        if not t.get("offline") and not t.get("fullyDelisted")
        and float(t.get("liquidity") or 0) >= args.min_liquidity
        and float(t.get("volume24h") or 0) >= args.min_volume
    ]


def fetch(args) -> None:
    conn = store.connect(args.db)
    tokens = research_universe(get_tokens(), args)
    store.save_tokens(conn, tokens, int(time.time()))
    print(f"tải {args.days} ngày nến {args.interval} cho {len(tokens)} coin...")

    def job(t):
        try:
            return t["alphaId"], fetch_history(t["alphaId"], args.interval, args.days)
        except Exception:
            return t["alphaId"], []

    total, done, t0 = 0, 0, time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for alpha_id, raws in pool.map(job, tokens):
            done += 1
            if raws:
                store.save_bars(conn, alpha_id, args.interval, raws)
                total += len(raws)
            if done % 25 == 0:
                conn.commit()
                print(f"  {done}/{len(tokens)} coin, {total:,} nến, {time.time()-t0:.0f}s")
    conn.commit()
    rng = conn.execute(
        "SELECT MIN(open_time), MAX(open_time) FROM bars WHERE interval=?", (args.interval,)
    ).fetchone()
    print(f"đã lưu {total:,} nến | từ {datetime.fromtimestamp(rng[0]/1000):%d/%m %H:%M} "
          f"đến {datetime.fromtimestamp(rng[1]/1000):%d/%m %H:%M}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--db", default="alpha.db")
    p.add_argument("--interval", default="5m")
    p.add_argument("--days", type=float, default=60.0, help="số ngày lịch sử cần tải")
    p.add_argument("--fetch", action="store_true", help="tải dữ liệu lịch sử trước khi phân tích")
    p.add_argument("--pump", type=float, default=10.0, help="định nghĩa pump: tăng tối đa (%%)")
    p.add_argument("--horizon", type=int, default=12, help="số nến nhìn tới trước")
    p.add_argument("--min-bar-volume", type=float, default=100.0, help="USD/nến tối thiểu")
    p.add_argument("--combo", action="store_true", help="dò điều kiện kết hợp")
    p.add_argument("--validate", action="store_true", help="kiểm định train/test theo thời gian")
    p.add_argument("--min-support", type=int, default=500, help="số mẫu tối thiểu cho một tổ hợp")
    p.add_argument("--no-tables", action="store_true", help="bỏ bảng lift từng đặc trưng")
    p.add_argument("--tp", type=float, default=8.0, help="chốt lời khi mô phỏng (%%)")
    p.add_argument("--sl", type=float, default=4.0, help="cắt lỗ khi mô phỏng (%%)")
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--min-liquidity", type=float, default=100000.0)
    p.add_argument("--min-volume", type=float, default=50000.0)
    args = p.parse_args()

    if args.fetch:
        fetch(args)

    conn = store.connect(args.db)
    t0 = time.time()
    data = load_dataset(conn, args)
    if data is None:
        print("Chưa có dữ liệu. Chạy: python3 research.py --fetch")
        return
    F, hits, gains, maes, times, _ = data
    print(f"\nMẫu: {len(F):,} | pump (+{args.pump}% trong {args.horizon} nến {args.interval}): "
          f"{int(hits.sum()):,} = {hits.mean()*100:.2f}% | nạp {time.time()-t0:.0f}s")

    if not args.no_tables:
        for name in NAMES:
            lift_table(F, hits, gains, name)
    if args.combo:
        combo_search(F, hits, gains, maes, args.min_support, args.tp, args.sl)
    if args.validate:
        validate(F, hits, gains, maes, times, args.min_support, args.tp, args.sl)


if __name__ == "__main__":
    main()
