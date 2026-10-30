# Hệ thống điều khiển xe — AI Arduino Control

Giao diện desktop Tkinter trong `dieukhienxe.py`, bố cục theo ảnh tham chiếu: nền tối, điều hướng bên trái, kết nối và đồng hồ ở giữa, camera và trạng thái xe bên phải. `main.cpp` là chương trình C++ mẫu, không phải điểm chạy của giao diện. Bản cũ trong `dieukhienngontay/` được giữ nguyên.

## Chạy

```powershell
python dieukhienxe.py --demo
```

Chế độ xem trước chỉ cần Python có Tkinter; không mở camera hoặc COM. Các nút hướng và thanh trượt có thể dùng để xem phản hồi giao diện.

Để chạy với phần cứng, cài thư viện trong môi trường riêng:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python dieukhienxe.py --port COM5
```

Chọn COM → **KẾT NỐI ARDUINO**. Giữ nút hướng hoặc W/A/S/D, thả để dừng. Phím mũi tên cũng được hỗ trợ. Space, Escape hoặc nút đỏ gửi lệnh dừng và tắt chế độ AI. Mất tiêu điểm cửa sổ cũng yêu cầu dừng.

**BẬT CAMERA** mở webcam ngay trong giao diện; dùng `--camera 1` nếu camera nằm ở chỉ số khác. Camera và COM chạy nền, Tkinter luôn chạy trên luồng chính. Không cần Arduino để xem camera. Khi thiếu thư viện, giao diện vẫn mở và thông báo lỗi ở thanh dưới cùng.

## Nhận diện bàn tay

Hỗ trợ MediaPipe Hands cũ và [Hand Landmarker Tasks](https://ai.google.dev/edge/mediapipe/solutions/vision/hand_landmarker/python). Với MediaPipe Tasks, tải model chính thức của Google một lần:

```powershell
New-Item -ItemType Directory -Force models
Invoke-WebRequest 'https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task' -OutFile models/hand_landmarker.task
```

Có thể chỉ định model khác bằng `--model PATH`. Nếu chưa có MediaPipe/model, camera vẫn hiển thị nhưng không cho bật điều khiển AI. Bật **Điều khiển bằng AI** sau khi đã kết nối COM và camera có nhận diện.

Nếu PowerShell gặp lỗi TLS khi tải model, có thể dùng Python:

```powershell
python -c "import urllib.request; urllib.request.urlretrieve('https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task', 'models/hand_landmarker.task')"
```

| Ngón tay | Lệnh | Hành động |
| --- | --- | --- |
| 1 | `1` | Tiến |
| 2 | `2` | Trái |
| 3 | `3` | Phải |
| 5 | `5` | Lùi |
| 0, 4 hoặc không thấy tay | `0` mặc định | Dừng |

Cử chỉ di chuyển cần ổn định 250 ms. Mất tay yêu cầu dừng ngay khi xử lý khung hình; không có khung hình mới trong 800 ms sẽ gửi dừng và tắt AI. Nút hướng không gửi lệnh trong chế độ AI để tránh hai nguồn điều khiển ghi đè nhau.

## Giao thức Arduino cần khớp với firmware

Repo chưa có firmware Arduino, dữ liệu cảm biến hoặc hệ số đổi PWM sang cm/s. Các lệnh hướng giữ nguyên từ Python cũ: ký tự ASCII đơn `1`, `2`, `3`, `5`, không có ký tự xuống dòng. Mặc định gửi `0` để dừng, phù hợp đường điều khiển AI cũ. Nếu firmware dùng `4` làm dừng như nút bấm cũ, chạy:

```powershell
python dieukhienxe.py --stop-command 4
```

Thanh trượt và đồng hồ hiển thị **mức tốc độ đặt theo %**, không phải tốc độ đo được. Mặc định chưa gửi tốc độ vì chưa biết giao thức firmware. Khi firmware đã nhận PWM dạng `V128\n`, có thể bật gửi bằng:

```powershell
python dieukhienxe.py --speed-template 'V{value}\n'
```

`{value}` được đổi từ 0–100% sang PWM 0–255. Đây là ví dụ giao thức tùy chọn, cần tự khớp với firmware thực tế.

Để hiện khoảng cách, Arduino gửi bản tin kết thúc bằng `\n`, chẳng hạn `DIST:25.4\n`, `DISTANCE=25.4 cm\n` hoặc `D:25.4\n`. Không nhận số trần để tránh nhầm echo lệnh. Không nhận dữ liệu trong 3 giây sẽ hiện `-- cm`.

Ứng dụng yêu cầu dừng trước khi ngắt COM và thoát. Nếu cáp đã bị rút hoặc tiến trình bị tắt cưỡng bức thì phần mềm không thể bảo đảm lệnh tới xe; firmware cần có cơ chế dừng khi hết thời gian nhận lệnh phù hợp với giao thức gửi khi thay đổi hiện tại.

## Kiểm tra

```powershell
python -m unittest discover -s tests -v
python -m py_compile dieukhienxe.py
```

Trên Windows, `python tools/preview_dashboard.py` chụp cửa sổ thực bằng Win32 PrintWindow vào `artifacts/dashboard-preview.png`, không mở camera/COM. Dùng `--size 1080x660 --output artifacts/dashboard-compact.png` để kiểm tra cửa sổ nhỏ.
