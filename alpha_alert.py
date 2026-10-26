"""Hệ thống alert coin Binance Alpha: pump giá + volume đột biến, dựa trên nến 1m.

Mỗi vòng quét: lọc watchlist theo thanh khoản -> tải nến 1m -> lưu SQLite ->
chấm tín hiệu bằng signals.analyze -> gửi alert.

Cách dùng:
    python3 alpha_alert.py                                  # +10%/15m, volume x3
    python3 alpha_alert.py --pct 12 --window 10 --vol-mult 5
    python3 alpha_alert.py --collect-only                   # chỉ thu dữ liệu để backtest
    nohup python3 -u alpha_alert.py > alert.log 2>&1 &

Telegram: export TELEGRAM_BOT_TOKEN=... và TELEGRAM_CHAT_ID=...
"""
import argparse
import os
import subprocess
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import store
from alpha_price import get_klines, get_tokens
from signals import Config, analyze, parse_kline

TRADE_URL_CHAIN = {"BSC": "bsc", "Base": "base", "Ethereum": "eth", "Solana": "sol"}
NOTIFY_SCRIPT = (
    "on run argv\n"
    'display notification (item 1 of argv) with title (item 2 of argv) sound name "Glass"\n'
    "end run"
)


def notify_macos(title: str, message: str) -> None:
    subprocess.run(["osascript", "-e", NOTIFY_SCRIPT, message, title], check=False)


def notify_telegram(message: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return
    data = urllib.parse.urlencode(
        {"chat_id": chat_id, "text": message, "disable_web_page_preview": "true"}
    ).encode()
    try:
        urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data, timeout=10).close()
    except Exception as e:
        print(f"  telegram lỗi: {e}")


def build_watchlist(tokens: list, args) -> list:
    """Chỉ giữ coin đủ thanh khoản, đang giao dịch và đã list đủ lâu."""
    now_ms = time.time() * 1000
    out = []
    for t in tokens:
        if t.get("offline") or t.get("offsell") or t.get("fullyDelisted"):
            continue
        if float(t.get("liquidity") or 0) < args.min_liquidity:
            continue
        if float(t.get("volume24h") or 0) < args.min_volume:
            continue
        listing = float(t.get("listingTime") or 0)
        if listing and now_ms - listing < args.min_age_hours * 3600 * 1000:
            continue
        out.append(t)
    return out


def slippage_pct(liquidity: float, size: float) -> float:
    """Ước lượng trượt giá theo mô hình AMM tích không đổi (gần đúng)."""
    return 200 * size / liquidity if liquidity > 0 else float("inf")


def trade_link(token: dict) -> str:
    chain = TRADE_URL_CHAIN.get(token.get("chainName", ""))
    if not chain or not token.get("contractAddress"):
        return ""
    return f"https://www.binance.com/en/alpha/{chain}/{token['contractAddress']}"


def fire_alert(token: dict, sig: dict, args, conn) -> None:
    liq = float(token.get("liquidity") or 0)
    fdv = float(token.get("fdv") or 0)
    slip = slippage_pct(liq, args.size)
    ts = datetime.now()
    head = (
        f"{token['symbol']} ({token.get('chainName')}) "
        f"+{sig['rise_pct']:.1f}%/{args.window}m | vol x{sig['vol_ratio']:.1f}"
    )
    detail = (
        f"giá {sig['ref_price']:.8g} -> {sig['price']:.8g} USD\n"
        f"volume {args.window}m: {sig['window_volume']:,.0f} USD / {sig['window_trades']} lệnh"
        f" (nền {sig['base_vol_per_bar']:,.0f} USD/phút)\n"
        f"biến động 60m: {sig['volatility']:.2f}%\n"
        f"thanh khoản {liq:,.0f} USD | FDV {fdv:,.0f} USD\n"
        f"trượt giá ước tính với {args.size:,.0f} USD: ~{slip:.2f}%\n"
        f"{token.get('contractAddress', '')}"
    )
    link = trade_link(token)
    print(f"[{ts:%H:%M:%S}] ALERT {head}")
    notify_macos("Binance Alpha pump", head)
    notify_telegram(f"{head}\n{detail}" + (f"\n{link}" if link else "") + f"\n{ts:%Y-%m-%d %H:%M:%S}")
    store.save_alert(
        conn,
        (
            int(ts.timestamp()), token["alphaId"], token["symbol"], sig["price"],
            round(sig["rise_pct"], 2), round(sig["vol_ratio"], 2), sig["window_volume"], liq, fdv,
        ),
    )


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--pct", type=float, default=10.0, help="giá tăng tối thiểu (%%)")
    p.add_argument("--window", type=int, default=15, help="cửa sổ tín hiệu (phút)")
    p.add_argument("--vol-mult", type=float, default=3.0, help="volume gấp N lần mức nền")
    p.add_argument("--min-window-volume", type=float, default=5000.0, help="USD khớp tối thiểu trong cửa sổ")
    p.add_argument("--min-volatility", type=float, default=0.0,
                   help="độ lệch chuẩn lợi suất 60 nến tối thiểu (%%), 0 = tắt")
    p.add_argument("--interval", type=float, default=30.0, help="chu kỳ quét (giây)")
    p.add_argument("--cooldown", type=float, default=30.0, help="phút giữa 2 alert cùng coin")
    p.add_argument("--min-liquidity", type=float, default=250000.0, help="thanh khoản tối thiểu (USD)")
    p.add_argument("--min-volume", type=float, default=200000.0, help="volume 24h tối thiểu (USD)")
    p.add_argument("--min-age-hours", type=float, default=2.0, help="bỏ qua coin mới list dưới N giờ")
    p.add_argument("--size", type=float, default=1000.0, help="size lệnh dự kiến (USD) để ước tính trượt giá")
    p.add_argument("--db", default="alpha.db", help="file SQLite lưu dữ liệu")
    p.add_argument("--workers", type=int, default=8, help="số request song song")
    p.add_argument("--collect-only", action="store_true", help="chỉ thu dữ liệu, không alert")
    args = p.parse_args()

    cfg = Config(
        window=args.window,
        pct=args.pct,
        vol_mult=args.vol_mult,
        min_window_volume=args.min_window_volume,
        min_volatility=args.min_volatility,
    )
    bars = cfg.window + cfg.baseline + 5
    conn = store.connect(args.db)
    last_alert: dict = {}
    watchlist: list = []
    watchlist_ts = 0.0

    print(
        f"[{datetime.now():%Y-%m-%d %H:%M:%S}] Alpha alert | +{cfg.pct}% & volume x{cfg.vol_mult} "
        f"trong {cfg.window}m | thanh khoản >= {args.min_liquidity:,.0f} USD | DB {args.db}"
    )

    while True:
        started = time.time()
        if started - watchlist_ts > 600:
            try:
                tokens = get_tokens()
                now_s = int(started)
                store.save_tokens(conn, tokens, now_s)
                store.save_snapshots(conn, tokens, now_s)
                watchlist = build_watchlist(tokens, args)
                watchlist_ts = started
                print(f"[{datetime.now():%H:%M:%S}] watchlist: {len(watchlist)} coin")
            except Exception as e:
                print(f"[{datetime.now():%H:%M:%S}] lỗi token list: {e}")

        def fetch(token):
            try:
                return token, get_klines(token["alphaId"], "1m", bars)
            except Exception:
                return token, []

        signals = 0
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            for token, raws in pool.map(fetch, watchlist):
                if not raws:
                    continue
                store.save_bars(conn, token["alphaId"], "1m", raws)
                if args.collect_only:
                    continue
                if started - last_alert.get(token["alphaId"], 0) < args.cooldown * 60:
                    continue
                sig = analyze([parse_kline(r) for r in raws], cfg)
                if not sig:
                    continue
                last_alert[token["alphaId"]] = started
                signals += 1
                fire_alert(token, sig, args, conn)
        conn.commit()

        elapsed = time.time() - started
        print(
            f"[{datetime.now():%H:%M:%S}] quét {len(watchlist)} coin trong {elapsed:.1f}s, "
            f"{signals} tín hiệu"
        )
        time.sleep(max(0.0, args.interval - elapsed))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nĐã dừng.")
