"""Logic tín hiệu dùng chung cho alert realtime và backtest."""
from dataclasses import dataclass
from statistics import median
from typing import List, NamedTuple, Optional


class Kline(NamedTuple):
    open_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    quote_volume: float
    trades: int


@dataclass
class Config:
    window: int = 15               # số nến 1m của cửa sổ tín hiệu
    pct: float = 10.0              # giá tăng tối thiểu trong cửa sổ (%)
    vol_mult: float = 3.0          # volume/phút trong cửa sổ gấp N lần mức nền
    baseline: int = 120            # số nến làm mức nền volume
    breakout: int = 60             # số nến trước cửa sổ dùng xác nhận đỉnh mới
    min_window_volume: float = 5000.0   # USD khớp trong cửa sổ, lọc pump rỗng
    min_volatility: float = 0.0    # độ lệch chuẩn lợi suất 60 nến (%), 0 = tắt


def volatility(klines: List[Kline], bars: int = 60) -> float:
    """Độ lệch chuẩn lợi suất từng nến trong `bars` nến gần nhất, tính theo %."""
    seg = klines[-bars - 1:]
    rets = [seg[i].close / seg[i - 1].close - 1 for i in range(1, len(seg)) if seg[i - 1].close]
    if len(rets) < 2:
        return 0.0
    m = sum(rets) / len(rets)
    return (sum((r - m) ** 2 for r in rets) / len(rets)) ** 0.5 * 100


def parse_kline(raw: list) -> Kline:
    return Kline(
        open_time=int(raw[0]),
        open=float(raw[1]),
        high=float(raw[2]),
        low=float(raw[3]),
        close=float(raw[4]),
        volume=float(raw[5]),
        quote_volume=float(raw[7]),
        trades=int(raw[8]),
    )


def analyze(klines: List[Kline], cfg: Config) -> Optional[dict]:
    """Trả về thông tin tín hiệu nếu nến cuối cùng thoả mọi điều kiện."""
    if len(klines) < cfg.window + 30:
        return None

    win = klines[-cfg.window:]
    ref = klines[-cfg.window - 1].close
    price = klines[-1].close
    if ref <= 0 or price <= 0:
        return None

    rise = (price / ref - 1) * 100
    if rise < cfg.pct:
        return None

    # phải là đỉnh mới, loại các cú hồi sau khi đã dump
    prior = klines[-cfg.window - cfg.breakout - 1:-cfg.window - 1]
    if prior and price < max(k.high for k in prior):
        return None

    win_vol = sum(k.quote_volume for k in win)
    if win_vol < cfg.min_window_volume:
        return None

    base = klines[-cfg.window - cfg.baseline - 1:-cfg.window - 1] or klines[:-cfg.window]
    base_vols = [k.quote_volume for k in base]
    base_per_bar = median(base_vols)
    if base_per_bar <= 0:
        base_per_bar = sum(base_vols) / len(base_vols) if base_vols else 0.0

    win_per_bar = win_vol / len(win)
    vol_ratio = win_per_bar / base_per_bar if base_per_bar > 0 else float("inf")
    if vol_ratio < cfg.vol_mult:
        return None

    vola = volatility(klines)
    if vola < cfg.min_volatility:
        return None

    return {
        "time": klines[-1].open_time,
        "price": price,
        "ref_price": ref,
        "rise_pct": rise,
        "vol_ratio": vol_ratio,
        "volatility": vola,
        "window_volume": win_vol,
        "window_trades": sum(k.trades for k in win),
        "base_vol_per_bar": base_per_bar,
        "window_high": max(k.high for k in win),
        "window_low": min(k.low for k in win),
    }
