import cv2
import mediapipe as mp
import serial
import time

# --- 1. KẾT NỐI ARDUINO ---
cong_arduino = 'COM5' # Nhớ kiểm tra lại cổng COM của bạn trong Arduino IDE
try:
    arduino = serial.Serial(cong_arduino, 9600, timeout=1)
    print(f"Đã kết nối Arduino ở {cong_arduino}")
except:
    print(f"LỖI: Không tìm thấy Arduino ở {cong_arduino}. Xe sẽ không chạy, nhưng Camera vẫn mở.")
    arduino = None

# --- 2. CÀI ĐẶT THỊ GIÁC MÁY TÍNH (MEDIAPIPE) ---
mp_hands = mp.solutions.hands
hands = mp_hands.Hands(max_num_hands=1, min_detection_confidence=0.7)
mp_draw = mp.solutions.drawing_utils

cap = cv2.VideoCapture(0) # Mở Webcam
tip_ids = [4, 8, 12, 16, 20] # ID các đầu ngón tay: Cái, Trỏ, Giữa, Áp út, Út

lenh_cu = '-1' # Tránh việc gửi liên tục 1 lệnh gây quá tải Arduino

while True:
    success, img = cap.read()
    if not success:
        break
        
    img = cv2.flip(img, 1) # Lật hình như soi gương cho dễ nhìn
    img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    results = hands.process(img_rgb)
    
    so_ngon_tay = 0 # Mặc định là 0 ngón (Áp dụng khi nắm tay hoặc rụt tay khỏi camera)

    if results.multi_hand_landmarks:
        for hand_landmarks in results.multi_hand_landmarks:
            mp_draw.draw_landmarks(img, hand_landmarks, mp_hands.HAND_CONNECTIONS)
            
            lm_list = []
            for id, lm in enumerate(hand_landmarks.landmark):
                h, w, c = img.shape
                cx, cy = int(lm.x * w), int(lm.y * h)
                lm_list.append([id, cx, cy])
            
            fingers = []
            # Kiểm tra Ngón cái (Dựa vào trục X)
            if lm_list[tip_ids[0]][1] > lm_list[tip_ids[0] - 1][1]:
                fingers.append(1)
            else:
                fingers.append(0)
                
            # Kiểm tra 4 ngón còn lại (Dựa vào trục Y - ngón giơ lên thì đầu ngón sẽ cao hơn khớp)
            for id in range(1, 5):
                if lm_list[tip_ids[id]][2] < lm_list[tip_ids[id] - 2][2]:
                    fingers.append(1)
                else:
                    fingers.append(0)
            
            so_ngon_tay = fingers.count(1)
            
    # --- 3. GỬI LỆNH XUỐNG ARDUINO ---
    # Phải đặt ngoài khối if để khi không có tay, nó vẫn gửi được số '0'
    lenh_moi = str(so_ngon_tay)
    
    # Đã thêm số '5' vào danh sách cho phép gửi
    if lenh_moi in ['0', '1', '2', '3', '4', '5'] and lenh_moi != lenh_cu:
        if arduino:
            arduino.write(lenh_moi.encode())
        print(f"Gửi lệnh: {lenh_moi} ({so_ngon_tay} ngón tay)")
        lenh_cu = lenh_moi

    # Hiển thị chữ lên màn hình
    cv2.putText(img, f'Ngon tay: {so_ngon_tay}', (20, 50), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 255, 0), 3)
    
    cv2.imshow("Dieu khien xe bang AI", img)
    
    # Bấm phím 'q' để thoát
    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

# --- 4. DỌN DẸP KHI TẮT CHƯƠNG TRÌNH ---
if arduino:
    arduino.write('0'.encode()) # Ép xe dừng hẳn trước khi ngắt kết nối
    arduino.close()
cap.release()
cv2.destroyAllWindows()