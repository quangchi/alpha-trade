"""Backtest tín hiệu pump trên dữ liệu nến đã thu thập trong SQLite.

Chạy sau khi alpha_alert.py đã thu dữ liệu một thời gian:
    python3 backtest.py                       # thống kê với cấu hình mặc định
    python3 backtest.py --list                # in từng tín hiệu
    python3 backtest.py --sweep               # dò nhiều ngưỡng pct / vol-mult
    python3 backtest.py --tp 10 --sl 5        # mô phỏng chốt lời / cắt lỗ
"""
import argparse
from datetime import datetime
from statistics import mean, median

import store
from signals import Config, analyze

HORIZONS = (5, 15, 30, 60)


def forward_stats(klines, i: int, horizon: int):
    future = klines[i + 1:i + 1 + horizon]
    if len(future) < horizon:
        return None
    entry = klines[i].close
    return {
        "ret": (future[-1].close / entry - 1) * 100,
        "max_gain": (max(k.high for k in future) / entry - 1) * 100,
        "max_dd": (min(k.low for k in future) / entry - 1) * 100,
    }


def simulate_tp_sl(klines, i: int, tp: float, sl: float, max_bars: int) -> str:
    entry = klines[i].close
    for k in klines[i + 1:i + 1 + max_bars]:
        # trong 1 nến không biết TP hay SL chạm trước, giả định xấu nhất là SL
        if k.low <= entry * (1 - sl / 100):
            return "sl"
        if k.high >= entry * (1 + tp / 100):
            return "tp"
    return "timeout"


def collect_signals(conn, cfg: Config, cooldown_bars: int):
    results = []
    for alpha_id, symbol, _ in store.list_symbols(conn):
        klines = store.load_bars(conn, alpha_id)
        need = cfg.window + 30
        last_hit = -10**9
        for i in range(need, len(klines)):
            if i - last_hit < cooldown_bars:
                continue
            sig = analyze(klines[:i + 1], cfg)
            if not sig:
                continue
            last_hit = i
            results.append((alpha_id, symbol, klines, i, sig))
    return results


def print_summary(results, klines_lookup=None, tp=None, sl=None, max_bars=60) -> None:
    print(f"\nTổng số tín hiệu: {len(results)}")
    if not results:
        print("Chưa đủ dữ liệu. Hãy chạy alpha_alert.py để thu thập thêm nến.")
        return

    print(f"\n{'Khung':>6}{'n':>6}{'Win%':>8}{'TB %':>9}{'Trung vị %':>12}{'Đỉnh TB %':>12}{'Đáy TB %':>11}")
    for h in HORIZONS:
        rows = [forward_stats(k, i, h) for _, _, k, i, _ in results]
        rows = [r for r in rows if r]
        if not rows:
            continue
        rets = [r["ret"] for r in rows]
        wins = sum(1 for r in rets if r > 0) / len(rets) * 100
        print(
            f"{h:>5}m{len(rows):>6}{wins:>8.1f}{mean(rets):>9.2f}{median(rets):>12.2f}"
            f"{mean(r['max_gain'] for r in rows):>12.2f}{mean(r['max_dd'] for r in rows):>11.2f}"
        )

    if tp is not None and sl is not None:
        outcomes = [simulate_tp_sl(k, i, tp, sl, max_bars) for _, _, k, i, _ in results]
        n = len(outcomes)
        c = {o: outcomes.count(o) for o in ("tp", "sl", "timeout")}
        pnl = (c["tp"] * tp - c["sl"] * sl) / n
        print(
            f"\nMô phỏng TP +{tp}% / SL -{sl}% trong {max_bars} phút: "
            f"TP {c['tp']} | SL {c['sl']} | hết giờ {c['timeout']} | "
            f"kỳ vọng {pnl:+.2f}%/lệnh (chưa tính phí & trượt giá)"
        )


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--db", default="alpha.db")
    p.add_argument("--pct", type=float, default=10.0)
    p.add_argument("--window", type=int, default=15)
    p.add_argument("--vol-mult", type=float, default=3.0)
    p.add_argument("--min-window-volume", type=float, default=5000.0)
    p.add_argument("--cooldown", type=int, default=30, help="số phút giữa 2 tín hiệu cùng coin")
    p.add_argument("--list", action="store_true", help="in chi tiết từng tín hiệu")
    p.add_argument("--sweep", action="store_true", help="dò nhiều ngưỡng")
    p.add_argument("--tp", type=float, help="chốt lời (%%)")
    p.add_argument("--sl", type=float, help="cắt lỗ (%%)")
    p.add_argument("--max-bars", type=int, default=60, help="số phút giữ lệnh tối đa khi mô phỏng")
    args = p.parse_args()

    conn = store.connect(args.db)
    rows = store.list_symbols(conn)
    total_bars = sum(r[2] for r in rows)
    print(f"DB {args.db}: {len(rows)} coin, {total_bars:,} nến 1m")

    if args.sweep:
        print(f"\n{'pct':>6}{'volx':>7}{'n':>6}{'Win%@15m':>11}{'TB %@15m':>11}{'TB %@60m':>11}")
        for pct in (5, 8, 10, 15, 20, 30):
            for vm in (2, 3, 5, 10):
                cfg = Config(window=args.window, pct=pct, vol_mult=vm,
                             min_window_volume=args.min_window_volume)
                res = collect_signals(conn, cfg, args.cooldown)
                f15 = [s for s in (forward_stats(k, i, 15) for _, _, k, i, _ in res) if s]
                f60 = [s for s in (forward_stats(k, i, 60) for _, _, k, i, _ in res) if s]
                if not f15:
                    print(f"{pct:>6}{vm:>7}{len(res):>6}{'-':>11}{'-':>11}{'-':>11}")
                    continue
                wins = sum(1 for s in f15 if s["ret"] > 0) / len(f15) * 100
                m60 = mean(s["ret"] for s in f60) if f60 else float("nan")
                print(f"{pct:>6}{vm:>7}{len(res):>6}{wins:>11.1f}"
                      f"{mean(s['ret'] for s in f15):>11.2f}{m60:>11.2f}")
        return

    cfg = Config(window=args.window, pct=args.pct, vol_mult=args.vol_mult,
                 min_window_volume=args.min_window_volume)
    results = collect_signals(conn, cfg, args.cooldown)

    if args.list:
        print(f"\n{'Thời điểm':<20}{'Coin':<10}{'Tăng %':>9}{'Vol x':>8}{'+15m %':>9}{'+60m %':>9}")
        for _, symbol, klines, i, sig in results:
            f15 = forward_stats(klines, i, 15)
            f60 = forward_stats(klines, i, 60)
            ts = datetime.fromtimestamp(sig["time"] / 1000)
            print(
                f"{ts:%Y-%m-%d %H:%M}    {symbol:<10}{sig['rise_pct']:>9.1f}{sig['vol_ratio']:>8.1f}"
                f"{(f15['ret'] if f15 else float('nan')):>9.2f}{(f60['ret'] if f60 else float('nan')):>9.2f}"
            )

    print_summary(results, tp=args.tp, sl=args.sl, max_bars=args.max_bars)


if __name__ == "__main__":
    main()
