"""Bộ lọc cảnh báo coin Binance Alpha trên khung 15m/30m.

Không phải hệ thống vào lệnh tự động: nghiên cứu cho thấy kỳ vọng lợi nhuận
sau phí xấp xỉ 0. Việc của nó là rút gọn hàng trăm coin xuống vài tình huống
đáng nhìn, kèm xác suất tăng VÀ xác suất giảm đo từ chính dữ liệu lịch sử.

Cách dùng:
    python3 watchlist.py                        # khung 30m
    python3 watchlist.py --interval 15m
    python3 watchlist.py --top 10 --quiet       # chỉ in bảng xếp hạng
    nohup python3 -u watchlist.py > watchlist.log 2>&1 &
"""
import argparse
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import numpy as np

import research
import store
from alpha_alert import (build_watchlist, notify_macos, notify_telegram,
                         slippage_pct, trade_link)
from alpha_price import get_klines, get_tokens
from signals import parse_kline
from simulate import apply_where, parse_where

RULES = {
    "15m": [
        ("nổ khối lượng", "vol_x_ngan>=6.9,bien_dong>=2.82"),
        ("đáy biên độ", "vi_tri_range<=0.12,bien_dong>=2.82"),
        ("gom sau sóng", "ret_dai>=16.6,vi_tri_range<=0.45"),
    ],
    "30m": [
        ("nổ khối lượng", "vol_x_ngan>=7.62,bien_dong>=4.10"),
        ("phá đỉnh", "vi_tri_range>=0.93,bien_dong>=4.10"),
        ("đáy biên độ", "vi_tri_range<=0.12,bien_dong>=4.10"),
        ("hồi sau giảm", "ret_vua<=-9.38,ret_dai>=26.76"),
    ],
    "1h": [
        ("nổ khối lượng", "vol_x_ngan>=7.83,bien_dong>=5.80"),
        ("phá đỉnh", "vi_tri_range>=0.92,bien_dong>=5.80"),
        ("sát đỉnh 240", "cach_dinh>=-6.32,bien_dong>=5.80"),
        ("đà tăng", "ret_vua>=17.13,bien_dong>=5.80"),
    ],
    "4h": [
        ("đà tăng + lệnh", "lenh_x>=9.44,ret_ngan>=13.28"),
        ("đà tăng + khối lượng", "vol_x_vua>=10.22,ret_ngan>=13.28"),
        ("sát đỉnh biên độ", "vi_tri_range>=0.75,bien_dong>=10.80"),
        ("sát đỉnh 240", "cach_dinh>=-14.46,bien_dong>=10.80"),
    ],
}
# mỗi khung: (horizon tính nhãn, USD/nến tối thiểu, ngưỡng gọi là tăng mạnh)
DEFAULTS = {"15m": (8, 300.0, 10.0), "30m": (6, 600.0, 10.0),
            "1h": (6, 1200.0, 15.0), "4h": (6, 4800.0, 25.0)}


def rule_stats(conn, args, rules):
    """Đo lại từng luật trên dữ liệu đã tải: % tăng, % giảm, mức lãi/lỗ điển hình."""
    data = research.load_dataset(conn, args)
    if data is None:
        return {}, 0.0
    F, _, gains, maes, times, _ = data
    span = (times.max() - times.min()) / 86_400_000
    print(f"nền thống kê: {len(F):,} mẫu {args.interval} / {span:.0f} ngày")
    out = {}
    for name, where in rules:
        m = apply_where(F, parse_where(where))
        n = int(m.sum())
        if n == 0:
            continue
        g, d = gains[m], maes[m]
        out[name] = {
            "n": n,
            "pump": float((g >= args.pump).mean() * 100),
            "drop": float((d <= -args.pump * 0.6).mean() * 100),
            "gain_med": float(np.median(g)),
            "mae_med": float(np.median(d)),
        }
    base_pump = float((gains >= args.pump).mean() * 100)
    return out, base_pump


def describe(feats) -> str:
    f = {n: feats[i] for n, i in research.IDX.items()}
    return (f"vol x{f['vol_x_ngan']:.1f} | lệnh x{f['lenh_x']:.1f} | "
            f"biến động {f['bien_dong']:.2f}% | vị trí biên độ {f['vi_tri_range']:.2f} | "
            f"cách đỉnh {f['cach_dinh']:+.1f}% | 15 nến {f['ret_vua']:+.1f}%")


def fire(token, name, feats, stat, base, args, horizon_min):
    liq = float(token.get("liquidity") or 0)
    slip = slippage_pct(liq, args.size)
    ts = datetime.now()
    head = f"{token['symbol']} ({token.get('chainName')}) · {name} · {args.interval}"
    body = (
        f"{describe(feats)}\n"
        f"lịch sử {stat['n']:,} lần khớp: {stat['pump']:.0f}% tăng ≥{args.pump:.0f}% "
        f"trong {horizon_min//60}h (nền {base:.1f}%) | "
        f"{stat['drop']:.0f}% giảm ≥{args.pump*0.6:.0f}%\n"
        f"trung vị: đỉnh {stat['gain_med']:+.1f}% / đáy {stat['mae_med']:+.1f}%\n"
        f"thanh khoản {liq:,.0f} USD | trượt ~{slip:.2f}% với {args.size:,.0f} USD"
    )
    link = trade_link(token)
    print(f"[{ts:%H:%M:%S}] >> {head}\n   {body.replace(chr(10), chr(10) + '   ')}")
    notify_macos("Alpha watchlist", head)
    notify_telegram(f"{head}\n{body}" + (f"\n{link}" if link else ""))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--interval", default="30m", choices=tuple(RULES))
    p.add_argument("--db", default="alpha.db")
    p.add_argument("--pump", type=float, default=None,
                   help="ngưỡng gọi là tăng mạnh (%%), mặc định theo khung")
    p.add_argument("--min-liquidity", type=float, default=250000.0)
    p.add_argument("--min-volume", type=float, default=200000.0)
    p.add_argument("--min-age-hours", type=float, default=2.0)
    p.add_argument("--size", type=float, default=1000.0, help="size lệnh để ước trượt giá")
    p.add_argument("--top", type=int, default=8, help="số dòng trong bảng xếp hạng")
    p.add_argument("--poll", type=float, default=60.0, help="chu kỳ quét (giây)")
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--quiet", action="store_true", help="không gửi thông báo, chỉ in bảng")
    args = p.parse_args()

    args.horizon, args.min_bar_volume, pump = DEFAULTS[args.interval]
    if args.pump is None:
        args.pump = pump
    rules = RULES[args.interval]
    step_ms = research.INTERVAL_MS[args.interval]
    horizon_min = args.horizon * step_ms // 60_000

    conn = store.connect(args.db)
    stats, base = rule_stats(conn, args, rules)
    if not stats:
        raise SystemExit(f"chưa có dữ liệu {args.interval}, chạy: "
                         f"python3 research.py --fetch --interval {args.interval} --days 300")
    print(f"xác suất nền: {base:.2f}% | luật đang theo dõi:")
    for name, where in rules:
        s = stats.get(name)
        if s:
            print(f"  {name:<20} {where:<42} {s['pump']:>5.1f}% tăng / "
                  f"{s['drop']:>5.1f}% giảm  (n={s['n']:,})")
    print("Kỳ vọng lợi nhuận tự động đo được ~0 sau phí: đây là bộ lọc để xem, "
          "không phải tín hiệu vào lệnh.\n")

    nbars = research.W_BASE + 10
    seen, watch, watch_ts, last_bar = set(), [], 0.0, 0
    while True:
        started = time.time()
        if started - watch_ts > 900:
            try:
                tokens = get_tokens()
                store.save_tokens(conn, tokens, int(started))
                watch = build_watchlist(tokens, args)
                watch_ts = started
                print(f"[{datetime.now():%H:%M:%S}] watchlist {len(watch)} coin")
            except Exception as e:
                print(f"[{datetime.now():%H:%M:%S}] lỗi token list: {e}")

        now_ms = started * 1000

        def scan(token):
            try:
                raws = get_klines(token["alphaId"], args.interval, nbars)
            except Exception:
                return None
            return (token, raws) if raws else None

        rows = []
        newest = 0
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            for res in pool.map(scan, watch):
                if not res:
                    continue
                token, raws = res
                store.save_bars(conn, token["alphaId"], args.interval, raws)
                bars = [parse_kline(r) for r in raws]
                if bars and bars[-1].open_time + step_ms > now_ms:
                    bars = bars[:-1]            # bỏ nến đang chạy
                if not bars:
                    continue
                newest = max(newest, bars[-1].open_time)
                feats = research.live_features(bars, args.min_bar_volume)
                if feats is None:
                    continue
                F1 = np.array([feats])
                for name, where in rules:
                    if not apply_where(F1, parse_where(where))[0]:
                        continue
                    stat = stats.get(name)
                    if not stat:
                        continue
                    rows.append((stat["pump"], token, name, feats, stat))
                    key = (token["alphaId"], name, bars[-1].open_time)
                    if key in seen or args.quiet:
                        continue
                    seen.add(key)
                    fire(token, name, feats, stat, base, args, horizon_min)
        conn.commit()

        rows.sort(key=lambda r: -r[0])
        stamp = datetime.now()
        if newest == last_bar:
            print(f"[{stamp:%H:%M:%S}] quét {len(watch)} coin, chưa có nến mới")
        else:
            last_bar = newest
            print(f"[{stamp:%H:%M:%S}] nến {datetime.fromtimestamp(newest/1000):%d/%m %H:%M} "
                  f"· quét {len(watch)} coin trong {time.time()-started:.1f}s "
                  f"· {len(rows)} coin khớp luật")
            for pump, token, name, feats, _ in rows[:args.top]:
                print(f"   {token['symbol']:<12} {name:<20} {pump:>5.1f}%  {describe(feats)}")

        time.sleep(max(0.0, args.poll - (time.time() - started)))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nĐã dừng.")
