# Novel Crawler

Novel Crawler là công cụ Python tải nội dung các chương truyện từ một trang mục lục hoặc một trang chương. Ứng dụng có giao diện Tkinter và dòng lệnh; nội dung được lưu thành các tệp `.txt`.

## Yêu cầu

- Python 3
- Tkinter nếu dùng giao diện đồ họa (thường được cài cùng Python trên Windows)

## Cài đặt

Mở PowerShell tại thư mục dự án và chạy:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Nếu PowerShell không nhận lệnh `py`, dùng `python` để tạo môi trường:

```powershell
python -m venv .venv
```

## Chạy giao diện đồ họa

Sau khi cài đặt, chạy:

```powershell
python gui.py
```

Nhập URL trang mục lục hoặc một chương, chọn chương bắt đầu và kết thúc (để trống chương kết thúc để tải đến hết), chọn thư mục lưu và nhấn **Bắt đầu**. Có thể nhấn **Dừng** để dừng sau chương hiện tại.

## Chạy dòng lệnh

```powershell
python crawl.py "https://example.com/truyen/ten-truyen"
```

Ví dụ giới hạn số chương để thử và chỉ định thư mục lưu:

```powershell
python crawl.py "https://example.com/truyen/ten-truyen" --max-chapters 5 --out "novels\ten-truyen"
```

Các tùy chọn thường dùng:

| Tùy chọn | Mặc định | Mô tả |
|---|---:|---|
| `-o`, `--out` | `novels\<domain>\<slug>` | Thư mục lưu các chương |
| `--delay` | `1.5` | Thời gian nghỉ giữa các request, tính bằng giây |
| `--max-chapters` | Không giới hạn | Giới hạn số chương tải |
| `--timeout` | `15.0` | Timeout cho mỗi request, tính bằng giây |
| `--retries` | `3` | Số lần thử lại khi request lỗi |
| `-v`, `--verbose` | Tắt | Hiển thị log chi tiết |
| `--ignore-robots` | Tắt | Bỏ qua robots.txt; chỉ dùng khi bạn có quyền và hiểu rõ trách nhiệm của mình |

Xem đầy đủ tùy chọn:

```powershell
python crawl.py --help
```

## Kết quả

Mỗi chương được lưu thành một tệp văn bản UTF-8, có số thứ tự và tiêu đề trong tên tệp, ví dụ `0001_Ten_chuong.txt`. Nếu không chỉ định thư mục đầu ra, tệp được lưu dưới `novels/<domain>/<slug>` tính từ thư mục đang chạy lệnh.

## Lưu ý

- Hãy tuân thủ điều khoản sử dụng, bản quyền và giới hạn truy cập của trang web. Tôn trọng `robots.txt` và giữ thời gian nghỉ hợp lý.
- Việc nhận diện mục lục, nội dung chương và liên kết chương tiếp theo dựa trên cấu trúc HTML phổ biến; một số trang có thể không được hỗ trợ chính xác.
- Khi chỉ cung cấp URL một chương, ứng dụng thử tìm mục lục trước; nếu không tìm thấy, ứng dụng lần theo liên kết chương tiếp theo.
