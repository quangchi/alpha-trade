"""Mô phỏng vào/thoát lệnh trên khung 15m - 30m.

Tín hiệu và lệnh chạy cùng một khung. Cắt lỗ mặc định theo GIÁ ĐÓNG NẾN để
không bị râu nến quét - đây là cách thực tế khi không ngồi nhìn bảng giá.

Cách dùng:
    python3 simulate.py --interval 30m --where "vol_x_ngan>=5,bien_dong>=1.3"
    python3 simulate.py --interval 15m --tp 12 --sl 6 --hold 8 --sl-mode wick
    python3 simulate.py --interval 30m --trail 6        # dừng lỗ dời theo đỉnh
"""
import argparse
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np

import research
import store
from alpha_price import get_klines
from signals import parse_kline

OPS = (">=", "<=", ">", "<")


def parse_where(text):
    conds = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        for op in OPS:
            if op in part:
                name, thr = part.split(op, 1)
                name = name.strip()
                if name not in research.IDX:
                    raise SystemExit(f"đặc trưng không tồn tại: {name}\n"
                                     f"chọn trong: {', '.join(research.IDX)}")
                conds.append((name, op, float(thr)))
                break
        else:
            raise SystemExit(f"không hiểu điều kiện: {part}")
    return conds


def apply_where(F, conds):
    mask = np.ones(len(F), dtype=bool)
    for name, op, thr in conds:
        col = F[:, research.IDX[name]]
        mask &= col >= thr if op == ">=" else col <= thr if op == "<=" else \
            col > thr if op == ">" else col < thr
    return mask


def run_trade(bars, tp, sl, cost, sl_mode, trail):
    """Vào lệnh ở giá mở cửa nến đầu tiên, trả về (kết quả, lãi lỗ %).

    Chế độ close chỉ ra quyết định tại giá đóng nến nên thoát đúng giá đóng,
    chấp nhận trượt qua mức stop - đây là điều thực sự xảy ra khi không canh lệnh.
    """
    if not bars:
        return None
    entry = bars[0].open
    if entry <= 0:
        return None
    peak = entry
    for b in bars:
        stop = peak * (1 - trail / 100) if trail else entry * (1 - sl / 100)
        target = entry * (1 + tp / 100) if tp else None
        kind = "trail" if trail and peak > entry else "sl"
        if sl_mode == "wick":
            if b.low <= stop:
                return kind, (stop / entry - 1) * 100 - cost
            if target and b.high >= target:
                return "tp", (target / entry - 1) * 100 - cost
            peak = max(peak, b.high)
        else:
            if b.close <= stop:
                return kind, (b.close / entry - 1) * 100 - cost
            if target and b.close >= target:
                return "tp", (b.close / entry - 1) * 100 - cost
            peak = max(peak, b.close)
    return "timeout", (bars[-1].close / entry - 1) * 100 - cost


def pick_signals(times, ids, mask, cooldown_bars, step_ms, cap):
    t, a = times[mask], ids[mask]
    order = np.argsort(t)
    t, a = t[order], a[order]
    gap = cooldown_bars * step_ms
    last, picked = {}, []
    for ti, ai in zip(t, a):
        if ti - last.get(ai, -10**15) < gap:
            continue
        last[ai] = ti
        picked.append((ai, int(ti)))
    if len(picked) > cap:
        s = len(picked) / cap
        picked = [picked[int(i * s)] for i in range(cap)]
    return picked


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--db", default="alpha.db")
    p.add_argument("--interval", default="30m",
                   choices=("15m", "30m", "1h", "4h"))
    p.add_argument("--where", default="vol_x_ngan>=5,bien_dong>=1.3")
    p.add_argument("--tp", type=float, default=12.0, help="0 = không chốt lời cố định")
    p.add_argument("--sl", type=float, default=6.0)
    p.add_argument("--trail", type=float, default=0.0, help="%% dời theo đỉnh, 0 = tắt")
    p.add_argument("--sl-mode", default="close", choices=("close", "wick"))
    p.add_argument("--hold", type=int, default=8, help="số nến giữ lệnh tối đa")
    p.add_argument("--cost", type=float, default=0.3, help="phí + trượt giá khứ hồi (%%)")
    p.add_argument("--cooldown", type=int, default=8, help="số nến giữa 2 lệnh cùng coin")
    p.add_argument("--test-only", action="store_true", help="chỉ 30%% thời gian cuối")
    p.add_argument("--max-signals", type=int, default=1500)
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--pump", type=float, default=10.0)
    p.add_argument("--horizon", type=int, default=8)
    p.add_argument("--min-bar-volume", type=float, default=500.0)
    args = p.parse_args()

    conds = parse_where(args.where)
    conn = store.connect(args.db)
    F, hits, gains, maes, times, ids = research.load_dataset(conn, args)
    mask = apply_where(F, conds)
    if args.test_only:
        mask &= times >= np.quantile(times, 0.7)

    step_ms = research.INTERVAL_MS[args.interval]
    picked = pick_signals(times, ids, mask, args.cooldown, step_ms, args.max_signals)
    exit_desc = f"trail {args.trail}%" if args.trail else f"SL -{args.sl}%"
    print(f"{args.interval} | {args.where} -> {int(mask.sum()):,} tín hiệu thô, "
          f"{len(picked):,} sau lọc trùng")
    print(f"TP +{args.tp}% | {exit_desc} ({args.sl_mode}) | giữ {args.hold} nến "
          f"({args.hold * step_ms // 60000} phút) | phí {args.cost}%")

    def job(item):
        aid, t = item
        for n in range(3):
            try:
                raw = get_klines(aid, args.interval, args.hold,
                                 start_time=t + step_ms)
                return [parse_kline(r) for r in raw]
            except Exception:
                time.sleep(1 + n)
        return []

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        seqs = [b for b in pool.map(job, picked) if b]

    res = [r for r in (run_trade(b, args.tp, args.sl, args.cost, args.sl_mode,
                                 args.trail) for b in seqs) if r]
    if not res:
        print("không lấy được dữ liệu nến cho các tín hiệu này")
        return

    pnl = np.array([r[1] for r in res])
    kinds = [r[0] for r in res]
    n = len(pnl)
    se = pnl.std(ddof=1) / np.sqrt(n)
    wins, losses = pnl[pnl > 0], pnl[pnl <= 0]
    print(f"\nsố lệnh: {n}")
    for k in ("tp", "sl", "trail", "timeout"):
        c = kinds.count(k)
        if c:
            print(f"  {k:<8} {c:>5} ({c/n*100:>5.1f}%)")
    print(f"kỳ vọng mỗi lệnh: {pnl.mean():+.3f}%  "
          f"[KTC 95%: {pnl.mean()-1.96*se:+.3f}%, {pnl.mean()+1.96*se:+.3f}%]")
    print(f"thắng {len(wins)/n*100:.1f}% TB {wins.mean() if len(wins) else 0:+.2f}% | "
          f"thua {len(losses)/n*100:.1f}% TB {losses.mean() if len(losses) else 0:+.2f}%")
    print(f"cộng dồn {pnl.sum():+.0f}% | trung vị {np.median(pnl):+.2f}%")


if __name__ == "__main__":
    main()
