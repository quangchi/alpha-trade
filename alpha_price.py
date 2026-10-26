"""Lấy giá coin Binance Alpha.

Cách dùng:
    python3 alpha_price.py                 # top 20 theo volume 24h
    python3 alpha_price.py AXIS BR         # giá theo symbol
    python3 alpha_price.py AXIS --live     # realtime (ticker USDT), Ctrl+C để dừng
    python3 alpha_price.py --top 50
"""
import argparse
import json
import time
import urllib.request
from typing import Optional

BASE = "https://www.binance.com/bapi/defi/v1/public"
TOKEN_LIST_URL = f"{BASE}/wallet-direct/buw/wallet/cex/alpha/all/token/list"
TICKER_URL = f"{BASE}/alpha-trade/ticker?symbol={{}}USDT"
KLINES_URL = f"{BASE}/alpha-trade/klines?symbol={{}}USDT&interval={{}}&limit={{}}"


def _get(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.load(resp)


def get_tokens() -> list:
    return _get(TOKEN_LIST_URL)["data"]


def get_klines(alpha_id: str, interval: str = "1m", limit: int = 120,
               start_time: Optional[int] = None, end_time: Optional[int] = None) -> list:
    """Nến của cặp ALPHA_xxxUSDT. Trả về [] nếu coin chưa có cặp giao dịch CEX."""
    url = KLINES_URL.format(alpha_id, interval, limit)
    if start_time is not None:
        url += f"&startTime={int(start_time)}"
    if end_time is not None:
        url += f"&endTime={int(end_time)}"
    data = _get(url).get("data")
    return data or []


def find_tokens(tokens: list, symbol: str) -> list:
    return [t for t in tokens if t["symbol"].upper() == symbol.upper() and not t.get("offline")]


def get_ticker(alpha_id: str) -> Optional[dict]:
    """Giá khớp lệnh gần nhất trên cặp ALPHA_xxxUSDT."""
    return _get(TICKER_URL.format(alpha_id)).get("data")


def print_tokens(tokens: list) -> None:
    print(f"{'SYMBOL':<12}{'ALPHA_ID':<14}{'CHAIN':<10}{'PRICE (USD)':>18}{'24H %':>9}{'VOL 24H':>16}")
    for t in tokens:
        print(
            f"{t['symbol']:<12}{t['alphaId']:<14}{t['chainName']:<10}"
            f"{float(t['price']):>18.8g}{t['percentChange24h']:>9}{float(t['volume24h']):>16,.0f}"
        )


def live(token: dict, interval: float) -> None:
    print(f"{token['symbol']} ({token['alphaId']}) - Ctrl+C để dừng")
    while True:
        data = get_ticker(token["alphaId"])
        if data:
            print(f"{time.strftime('%H:%M:%S')}  {token['symbol']}  {data['lastPrice']} USDT  ({data['priceChangePercent']}%)")
        time.sleep(interval)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("symbols", nargs="*")
    p.add_argument("--top", type=int, default=20)
    p.add_argument("--live", action="store_true")
    p.add_argument("--interval", type=float, default=2.0)
    args = p.parse_args()

    tokens = get_tokens()
    if not args.symbols:
        tokens = [t for t in tokens if not t.get("offline")]
        tokens.sort(key=lambda t: float(t["volume24h"] or 0), reverse=True)
        print_tokens(tokens[: args.top])
        return

    matched = []
    for s in args.symbols:
        found = find_tokens(tokens, s)
        if not found:
            print(f"Không tìm thấy symbol: {s}")
        matched += found
    if not matched:
        return

    if args.live:
        try:
            live(matched[0], args.interval)
        except KeyboardInterrupt:
            pass
    else:
        print_tokens(matched)


if __name__ == "__main__":
    main()
