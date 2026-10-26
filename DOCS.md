# Alpha Trade — tài liệu sử dụng

Hệ thống lọc và nghiên cứu coin Binance Alpha. Mục tiêu: rút gọn ~700 coin
xuống vài tình huống đáng xem mỗi nến, kèm xác suất tăng **và** xác suất giảm
đo từ dữ liệu lịch sử thật.

> **Đây không phải hệ thống vào lệnh tự động.** Toàn bộ kiểm định cho thấy kỳ
> vọng lợi nhuận sau phí xấp xỉ 0. Chi tiết ở mục [Kết quả kiểm định](#kết-quả-kiểm-định).

---

## 1. Cài đặt

Chỉ cần Python 3.9+ và `numpy`:

```sh
pip3 install numpy
```

Thông báo Telegram (tuỳ chọn):

```sh
export TELEGRAM_BOT_TOKEN=...
export TELEGRAM_CHAT_ID=...
```

Thông báo macOS chạy sẵn qua `osascript`, không cần cài gì.

---

## 2. Bắt đầu nhanh

```sh
# 1. Tải lịch sử (chạy một lần cho mỗi khung bạn định dùng)
python3 research.py --fetch --interval 30m --days 300
python3 research.py --fetch --interval 4h  --days 700

# 2. Chạy bộ lọc cảnh báo
python3 watchlist.py --interval 30m
```

Chạy nền:

```sh
nohup python3 -u watchlist.py --interval 4h > watchlist.log 2>&1 &
```

---

## 3. Các file

| File | Vai trò |
|---|---|
| `alpha_price.py` | Lớp gọi API Binance Alpha + CLI xem giá |
| `store.py` | Lưu trữ SQLite (`alpha.db`) |
| `research.py` | Tải lịch sử, tính đặc trưng, dò luật, kiểm định train/test |
| `simulate.py` | Mô phỏng vào/thoát lệnh để đo kỳ vọng lợi nhuận thật |
| `watchlist.py` | **Bộ lọc cảnh báo chính**, chạy liên tục |
| `signals.py` | Logic tín hiệu cũ dùng cho nến 1m |
| `alpha_alert.py` | Hệ thống alert cũ trên nến 1m, còn dùng lại hạ tầng thông báo |
| `backtest.py` | Backtest cho logic 1m cũ |

---

## 4. Phương pháp lọc

### 4.1. Nguồn dữ liệu

API công khai, không cần API key:

- Danh sách token: `/wallet-direct/buw/wallet/cex/alpha/all/token/list`
- Nến: `/alpha-trade/klines?symbol=ALPHA_xxxUSDT&interval=...&limit=...`

Giới hạn `limit` tối đa **1500** nến/lần, lùi xa hơn bằng phân trang `startTime`.

> **Cảnh báo quan trọng:** trường `volume24h` trong danh sách token là volume
> **on-chain DEX**, không phải volume khớp trên Binance Alpha. Có coin hiện
> 145 triệu USD on-chain nhưng volume nến bằng 0 — đó là wash trading để cày
> điểm Alpha. Vì vậy **mọi tính toán đều dùng `quoteVolume` của nến**, không
> bao giờ dùng `volume24h` để chấm tín hiệu.

Độ dài lịch sử khả dụng theo khung (đo thực tế):

| Khung | Lùi được tới | Số nến đã tải | Mẫu phân tích |
|---|---|---|---|
| 5m | 60 ngày | 2,513,176 | 998,998 |
| 15m | 147 ngày | 2,022,412 | 829,292 |
| 30m | 295 ngày | 1,898,090 | 780,710 |
| 1h | 542 ngày | 1,316,327 | 578,760 |
| 4h | 515 ngày | 329,134 | 134,235 |

### 4.2. Vũ trụ coin

Lọc trước khi tính toán:

- bỏ coin `offline`, `offsell`, `fullyDelisted`
- thanh khoản ≥ `--min-liquidity` (mặc định 250,000 USD)
- volume 24h ≥ `--min-volume` (mặc định 200,000 USD)
- đã list ≥ `--min-age-hours` (mặc định 2 giờ)

Còn lại khoảng **114 coin** theo dõi trực tiếp, **172 coin** trong tập nghiên cứu.

### 4.3. 11 đặc trưng

Mọi đặc trưng chỉ dùng dữ liệu **quá khứ tại thời điểm t** — không nhìn tương lai.
Cửa sổ tính bằng số nến: ngắn `W_S=3`, vừa `W_M=15`, dài `W_L=60`, nền `W_BASE=240`.

| Tên | Ý nghĩa |
|---|---|
| `vol_x_ngan` | volume/nến của 3 nến gần nhất so với mức nền |
| `vol_x_vua` | volume/nến của 15 nến gần nhất so với mức nền |
| `lenh_x` | số lệnh/nến của 15 nến gần nhất so với nền |
| `size_lenh_x` | giá trị trung bình mỗi lệnh so với nền |
| `ret_ngan` | % thay đổi giá qua 3 nến |
| `ret_vua` | % thay đổi giá qua 15 nến |
| `ret_dai` | % thay đổi giá qua 60 nến |
| `vi_tri_range` | vị trí giá trong biên độ 60 nến (0 = đáy, 1 = đỉnh) |
| `cach_dinh` | % cách đỉnh 240 nến |
| `ty_le_hoat_dong` | tỷ lệ nến có giao dịch trong 15 nến |
| `bien_dong` | độ lệch chuẩn lợi suất 60 nến (%) |

"Mức nền" = trung bình từ nến `t-240` đến `t-15`, tức là loại bỏ chính giai đoạn
đang xét ra khỏi mẫu so sánh.

### 4.4. Nhãn

Một mẫu được coi là **pump** nếu giá cao nhất trong `horizon` nến kế tiếp vượt
`pump%` so với giá đóng hiện tại. Ngưỡng nâng theo độ dài khung để xác suất nền
không bị phình:

| Khung | Ngưỡng pump | Horizon | Tương đương |
|---|---|---|---|
| 15m | +10% | 8 nến | 2 giờ |
| 30m | +10% | 6 nến | 3 giờ |
| 1h | +15% | 6 nến | 6 giờ |
| 4h | +25% | 6 nến | 24 giờ |

Song song đó hệ thống ghi lại **MAE** (mức giảm sâu nhất trong cùng khoảng) để
đo rủi ro, không chỉ đo phần thưởng.

### 4.5. Cách dò luật

1. Với mỗi đặc trưng, cắt ngưỡng tại các phân vị 0.5 / 0.7 / 0.85 / 0.95 theo
   cả hai chiều → khoảng 88 điều kiện đơn.
2. Ghép từng cặp điều kiện, giữ lại tổ hợp có đủ `--min-support` mẫu.
3. Xếp hạng theo **lift** = (tỷ lệ pump trong nhóm) / (tỷ lệ pump nền).
4. **Kiểm định train/test theo thời gian**: 70% thời gian đầu để tìm luật,
   30% sau để kiểm chứng. Luật nào chỉ đẹp ở train là overfit.

### 4.6. Luật đang dùng trong `watchlist.py`

Ngưỡng lấy từ bước dò luật ở trên, với số liệu đo lại tại thời điểm khởi động.

**Khung 30m** (xác suất nền 4.39%):

| Luật | Điều kiện | Tăng | Giảm | n |
|---|---|---|---|---|
| nổ khối lượng | `vol_x_ngan≥7.62` và `bien_dong≥4.10` | 38.4% | 60.8% | 5,098 |
| phá đỉnh | `vi_tri_range≥0.93` và `bien_dong≥4.10` | 40.5% | 61.6% | 1,392 |
| đáy biên độ | `vi_tri_range≤0.12` và `bien_dong≥4.10` | 24.6% | 34.0% | 6,134 |
| hồi sau giảm | `ret_vua≤-9.38` và `ret_dai≥26.76` | 32.9% | 43.3% | 2,416 |

**Khung 4h** (xác suất nền 6.70%):

| Luật | Điều kiện | Tăng | Giảm | n |
|---|---|---|---|---|
| đà tăng + lệnh | `lenh_x≥9.44` và `ret_ngan≥13.28` | 41.6% | 54.0% | 854 |
| đà tăng + khối lượng | `vol_x_vua≥10.22` và `ret_ngan≥13.28` | 39.8% | 52.3% | 902 |
| sát đỉnh biên độ | `vi_tri_range≥0.75` và `bien_dong≥10.80` | 39.1% | 47.6% | 843 |
| sát đỉnh 240 | `cach_dinh≥-14.46` và `bien_dong≥10.80` | 42.3% | 51.4% | 539 |

Cột "Giảm" = tỷ lệ chạm mức giảm bằng 60% ngưỡng pump trong cùng khoảng thời gian.
**Luôn đọc hai cột cùng lúc.** Luật "phá đỉnh" có 40.5% tăng nghe hấp dẫn, nhưng
61.6% giảm nằm ngay cạnh.

Khung 4h là khung duy nhất có luật mà tỷ lệ tăng xấp xỉ tỷ lệ giảm.

---

## 5. Lệnh chi tiết

### `watchlist.py` — bộ lọc cảnh báo

```sh
python3 watchlist.py [tuỳ chọn]
```

| Tham số | Mặc định | Ý nghĩa |
|---|---|---|
| `--interval` | `30m` | khung: `15m`, `30m`, `1h`, `4h` |
| `--pump` | theo khung | ngưỡng gọi là tăng mạnh (%) |
| `--min-liquidity` | 250000 | thanh khoản tối thiểu (USD) |
| `--min-volume` | 200000 | volume 24h tối thiểu (USD) |
| `--min-age-hours` | 2 | bỏ coin mới list |
| `--size` | 1000 | size lệnh dự kiến, để ước trượt giá |
| `--top` | 8 | số dòng trong bảng xếp hạng |
| `--poll` | 60 | chu kỳ quét (giây) |
| `--quiet` | tắt | không gửi thông báo, chỉ in bảng |

Khi khởi động, nó đo lại toàn bộ luật trên dữ liệu trong `alpha.db` rồi mới bắt
đầu theo dõi — số liệu trên cảnh báo luôn là số thật, không phải hằng số gõ cứng.

Mỗi coin khớp luật chỉ gửi **một thông báo cho mỗi nến**. Bảng xếp hạng chỉ in
khi có nến mới đóng.

### `research.py` — nghiên cứu

```sh
# tải lịch sử
python3 research.py --fetch --interval 4h --days 700

# bảng lift từng đặc trưng
python3 research.py --interval 30m --pump 10 --horizon 6

# dò điều kiện kết hợp
python3 research.py --interval 30m --combo --min-support 400

# kiểm định train/test + kỳ vọng TP/SL
python3 research.py --interval 4h --pump 25 --horizon 6 \
    --min-bar-volume 4800 --no-tables --validate --min-support 150 --tp 30 --sl 15
```

| Tham số | Ý nghĩa |
|---|---|
| `--fetch` | tải lịch sử, phân trang qua `startTime` |
| `--days` | số ngày cần tải |
| `--pump` / `--horizon` | định nghĩa nhãn |
| `--min-bar-volume` | USD/nến tối thiểu, loại coin chết thanh khoản |
| `--combo` | dò điều kiện kết hợp |
| `--validate` | tách train 70% / test 30% theo thời gian |
| `--tp` / `--sl` | ước lượng nhanh kỳ vọng lợi nhuận |
| `--no-tables` | bỏ bảng lift từng đặc trưng cho gọn |

`--min-bar-volume` nên tăng theo khung: 15m→300, 30m→600, 1h→1200, 4h→4800.

### `simulate.py` — đo kỳ vọng lợi nhuận thật

```sh
python3 simulate.py --interval 4h --where "vol_x_vua>=10.22,ret_ngan>=13.28" \
    --pump 25 --horizon 6 --min-bar-volume 4800 --trail 15 --hold 12
```

| Tham số | Mặc định | Ý nghĩa |
|---|---|---|
| `--where` | | điều kiện, phân tách bằng dấu phẩy, dùng `>=` `<=` `>` `<` |
| `--tp` / `--sl` | 12 / 6 | chốt lời / cắt lỗ (%), `--tp 0` để tắt |
| `--trail` | 0 | % dừng lỗ dời theo đỉnh, khác 0 sẽ thay thế `--sl` |
| `--sl-mode` | `close` | `close` = quyết định theo giá đóng nến, `wick` = theo râu nến |
| `--hold` | 8 | số nến giữ lệnh tối đa |
| `--cost` | 0.3 | phí + trượt giá khứ hồi (%) |
| `--cooldown` | 8 | số nến giữa 2 lệnh cùng coin |
| `--test-only` | tắt | chỉ dùng 30% thời gian cuối |

**Hai chế độ thoát lệnh cho ra hai cận của sự thật:**

- `--sl-mode close` (**bi quan**): chỉ ra quyết định tại giá đóng nến, thoát đúng
  giá đóng, chấp nhận trượt qua mức stop. Đây là điều xảy ra khi bạn không ngồi
  canh bảng giá.
- `--sl-mode wick` (**lạc quan**): giả định thoát đúng tại mức stop khi giá chạm
  tới trong nến.

Kết quả thật nằm giữa hai cận. Nếu cận bi quan âm và cận lạc quan dương thì
**chưa kết luận được gì** — đó là tình trạng của gần như mọi luật trong hệ thống này.

### `alpha_price.py` — xem giá

```sh
python3 alpha_price.py                 # top 20 coin
python3 alpha_price.py APR JCT         # coin cụ thể
python3 alpha_price.py APR --live      # cập nhật liên tục
```

---

## 6. Kết quả kiểm định

Phần này quan trọng hơn phần hướng dẫn. Tóm tắt những gì dữ liệu đã nói.

### 6.1. Khả năng dự báo là thật

Kiểm định out-of-sample, lift giữ nguyên qua hai giai đoạn thời gian:

| Khung | Luật | lift train | lift test | % pump (test) |
|---|---|---|---|---|
| 5m | `vol_x_ngan≥8.55` và `bien_dong≥1.61` | 17.85 | 19.59 | 30.2% |
| 15m | `vol_x_ngan≥6.9` và `bien_dong≥2.82` | 10.1 | 12.2 | 31.1% |
| 30m | `vol_x_ngan≥7.62` và `bien_dong≥4.10` | 7.97 | 11.12 | 39.6% |

Hệ thống thật sự tăng xác suất bắt được coin pump lên **10–20 lần** so với chọn ngẫu nhiên.

### 6.2. Nhưng dự báo đúng không tạo ra lợi nhuận

Mô phỏng thật với phí + trượt giá 0.3%:

| Khung | Luật nổ khối lượng, TP/SL cố định | Kết quả |
|---|---|---|
| 5m | TP+8 / SL-4 | −0.38% mỗi lệnh |
| 15m | TP+12 / SL-6 | −0.58% |
| 30m | TP+12 / SL-6 | **−1.57%** |
| 1h | TP+20 / SL-10 | −0.72% |

Lý do: coin pump 30–40% số lần, nhưng **50–62% số lần nó giảm sâu trước khi kịp lên**.
Đây là coin biến động cực mạnh, đi cả hai chiều. Xác suất tăng gấp 12 lần thì
xác suất giảm cũng gấp gần như vậy.

Và tín hiệu càng chậm thì vào lệnh càng muộn: luật nổ khối lượng tệ nhất ở khung
30m (−1.57%) chứ không phải khung nhanh.

### 6.3. Nhóm đối chứng ngang bằng mọi luật tinh vi

Thử nghiệm quyết định trên khung 4h, trailing stop 15%:

| Luật | n | EV | KTC 95% |
|---|---|---|---|
| chặt `vol_x_vua≥10.2, ret_ngan≥13.3` | 358 | +1.98% | [−0.59, +4.56] |
| vừa `vol_x_vua≥6, ret_ngan≥10` | 677 | +0.81% | [−0.87, +2.49] |
| rộng `vol_x_vua≥4, ret_ngan≥7` | 1,125 | +0.53% | [−0.57, +1.64] |
| **đối chứng — chỉ cần `bien_dong≥6`** | 1,500 | **+1.08%** | **[+0.04, +2.13]** |

Nhóm đối chứng — chọn bất kỳ coin nào đang biến động mạnh, không lọc gì thêm —
là luật **duy nhất có ý nghĩa thống kê**. Lợi thế đến từ **trailing stop trên
khung chậm**, không phải từ bộ lọc tín hiệu. Các luật chỉ làm giảm số mẫu.

Lưu ý con số +1.08% đo ở chế độ lạc quan. Cùng luật ở chế độ bi quan là −0.59%.

### 6.4. Những giả thuyết đã bị bác bỏ

| Giả thuyết | Kết quả |
|---|---|
| Volume tăng mạnh khi giá đi ngang = tích luỹ trước pump | **Sai.** Tỷ lệ pump 0.5% so với nền 2.86% (lift 0.2x). Nhiều khả năng là wash trading cày điểm Alpha. |
| Khung lớn ít nhiễu hơn nên có lợi nhuận hơn | **Sai.** Khung 30m tệ hơn khung 5m với cùng kiểu luật. |
| Cắt lỗ theo giá đóng nến tránh bị râu nến quét sẽ cứu được kỳ vọng | **Sai.** Vẫn âm, vì giá đóng thường nằm thấp hơn mức stop. |
| Tối ưu tỷ lệ TP/SL sẽ tìm được cấu hình dương | **Sai.** Đã quét 11 cấu hình trên khung 5m, toàn bộ đều âm. |

### 6.5. Giới hạn cần biết

- **Mẫu chồng lấn**: các mẫu liền kề nhau chia sẻ phần lớn dữ liệu, nên số mẫu
  hiệu dụng nhỏ hơn con số hiển thị. Khoảng tin cậy thật rộng hơn báo cáo.
- **Thiên lệch sống sót**: danh sách token là coin đang còn niêm yết hôm nay.
  Coin đã bị huỷ niêm yết không có trong dữ liệu.
- **Một chế độ thị trường duy nhất**: toàn bộ dữ liệu nằm trong 2025–2026.
- **Trượt giá**: với lệnh 500–2000 USD trên coin thanh khoản 250–300k, trượt giá
  ước tính 0.7–1.3% mỗi chiều — lớn hơn mọi biên lợi nhuận đo được ở trên.

---

## 7. Cách dùng đúng

Hệ thống này **giỏi dự báo biến động, nhưng biến động đi cả hai chiều**.

Dùng nó như một bộ lọc để thu hẹp tầm nhìn, không phải máy ra quyết định:

1. Chạy `watchlist.py` ở khung phù hợp với nhịp của bạn.
2. Khi có cảnh báo, đọc **cả hai cột** tăng/giảm.
3. Ưu tiên luật có khoảng cách tăng/giảm hẹp (`đáy biên độ` ở 30m, `sát đỉnh
   biên độ` ở 4h).
4. Kiểm tra thanh khoản và trượt giá ước tính trước khi tính size.
5. Tự quyết định vào lệnh.

Nếu muốn nghiên cứu tiếp, hướng duy nhất dữ liệu còn cho tín hiệu dương là
**trailing stop trên khung chậm**, và nó cần được kiểm chứng riêng với nhiều
mẫu hơn trước khi tin.
