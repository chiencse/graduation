# totnghiep.mchieens.io.vn

Trang web kiêm thiệp mời Lễ tốt nghiệp của Nông Minh Chiến (Khoa học Máy tính, ĐH Bách Khoa TP.HCM).

## Cấu trúc

| Đường dẫn | Nội dung |
|---|---|
| `server.py` | Server Python (chỉ dùng thư viện chuẩn): phục vụ `public/`, API xác nhận tham dự và sổ lưu bút, lưu bằng SQLite |
| `admin.html` | Trang `/admin`: thống kê khách, bảng xác nhận, xuất CSV, ẩn/hiện lời chúc, tạo link mời riêng |
| `public/index.html` | Trang chính (theme Bình minh) |
| `public/preview/index.html` | Bản thử, có nút đổi 3 theme (`?theme=dawn\|paper\|stars`) |
| `public/assets/` | Logo, ảnh trường, ảnh theo từng năm (`photos/<năm>-<tên>{,-sm}.jpg`) |
| `public/og.jpg` | Ảnh xem trước khi gửi link (1200×630) |
| `tools/og/og.html` | Trang dựng ảnh `og.jpg` (chụp bằng Chrome headless) |
| `deploy/` | systemd user units và cấu hình Cloudflare Tunnel |

Không có trong repo: `admin.token` (mã quản trị), `data/` (database khách), `backup/`.

## Chạy

```bash
APP_DIR=$PWD python3 server.py   # http://127.0.0.1:8088
```

Lần chạy đầu tiên, server tự tạo `data/site.db` và `admin.token`.

## Deploy (home-server)

- `systemctl --user restart totnghiep-web`: chỉ cần khi sửa `server.py`. File tĩnh thì chép đè là xong.
- Tunnel: `totnghiep-tunnel` chạy `cloudflared --config ~/.cloudflared/totnghiep.yml tunnel run`. File credentials của tunnel không nằm trong repo.
- Link mời riêng: `https://totnghiep.mchieens.io.vn/?to=Tên khách`
