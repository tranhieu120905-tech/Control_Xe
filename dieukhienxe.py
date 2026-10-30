import cv2
import mediapipe as mp
import serial
import time
import tkinter as tk
import threading

# ==========================================
# 1. KẾT NỐI ARDUINO
# ==========================================
# THAY ĐỔI 'COM5' THÀNH CỔNG CỦA BẠN (VD: 'COM3', 'COM5')
try:
    arduino = serial.Serial('COM5', 9600, timeout=1)
    print("Đã kết nối với Arduino!")
except:
    print("LỖI: Không tìm thấy Arduino. Hãy kiểm tra lại cổng COM.")
    arduino = None

# ==========================================
# 2. HỆ THỐNG AI ĐẾM NGÓN TAY (MediaPipe)
# ==========================================
mp_hands = mp.solutions.hands
hands = mp_hands.Hands(min_detection_confidence=0.7, min_tracking_confidence=0.7)
mp_draw = mp.solutions.drawing_utils

# Hàm đếm số ngón tay đang giơ lên
def count_fingers(hand_landmarks):
    # Các điểm đầu ngón tay
    tip_ids = [4, 8, 12, 16, 20]
    fingers = []
    
    # Ngón cái (Tính theo tọa độ X so với khớp)
    if hand_landmarks.landmark[tip_ids[0]].x > hand_landmarks.landmark[tip_ids[0] - 1].x:
        fingers.append(1)
    else:
        fingers.append(0)
        
    # 4 ngón còn lại (Tính theo tọa độ Y so với khớp)
    for id in range(1, 5):
        if hand_landmarks.landmark[tip_ids[id]].y < hand_landmarks.landmark[tip_ids[id] - 2].y:
            fingers.append(1)
        else:
            fingers.append(0)
            
    return fingers.count(1)

# Hàm gửi lệnh xuống Arduino
def send_command(command):
    if arduino:
        arduino.write(command.encode())
        print(f"Đã gửi lệnh: {command}")

# ==========================================
# 3. GIAO DIỆN GUI (Tkinter)
# ==========================================
def run_gui():
    root = tk.Tk()
    root.title("Bảng Điều Khiển Xe Thông Minh")
    root.geometry("300x250")
    
    tk.Label(root, text="ĐIỀU KHIỂN BẰNG NÚT BẤM", font=("Arial", 12, "bold")).pack(pady=10)
    
    tk.Button(root, text="TIẾN (1)", width=10, bg="green", fg="white", command=lambda: send_command('1')).pack()
    
    frame = tk.Frame(root)
    frame.pack(pady=5)
    tk.Button(frame, text="TRÁI (2)", width=10, bg="orange", command=lambda: send_command('2')).pack(side=tk.LEFT, padx=5)
    tk.Button(frame, text="DỪNG (4)", width=10, bg="red", fg="white", command=lambda: send_command('4')).pack(side=tk.LEFT, padx=5)
    tk.Button(frame, text="PHẢI (3)", width=10, bg="orange", command=lambda: send_command('3')).pack(side=tk.LEFT, padx=5)
    
    tk.Button(root, text="LÙI (5)", width=10, bg="blue", fg="white", command=lambda: send_command('5')).pack()
    
    root.mainloop()

# Chạy Giao diện (GUI) ở một luồng (Thread) riêng biệt để không làm đơ Camera
gui_thread = threading.Thread(target=run_gui)
gui_thread.daemon = True
gui_thread.start()

# ==========================================
# 4. CHẠY CAMERA VÀ AI
# ==========================================
cap = cv2.VideoCapture(0)
last_command = '0'

while True:
    success, img = cap.read()
    if not success:
        break
        
    img = cv2.flip(img, 1) # Lật gương hình ảnh
    img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    results = hands.process(img_rgb)
    
    finger_count = 0
    if results.multi_hand_landmarks:
        for hand_landmarks in results.multi_hand_landmarks:
            mp_draw.draw_landmarks(img, hand_landmarks, mp_hands.HAND_CONNECTIONS)
            finger_count = count_fingers(hand_landmarks)
            
    # Chuyển đổi số ngón tay thành lệnh
    current_command = '0' # Mặc định dừng
    if finger_count == 1: current_command = '1'
    elif finger_count == 2: current_command = '2'
    elif finger_count == 3: current_command = '3'
    elif finger_count == 5: current_command = '5'
    
    # Chỉ gửi lệnh nếu số ngón tay thay đổi (tránh gửi liên tục làm nghẽn cáp USB)
    if current_command != last_command:
        send_command(current_command)
        last_command = current_command

    # Hiển thị số ngón tay và lệnh lên màn hình Camera
    cv2.putText(img, f"Ngon tay: {finger_count}", (10, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 0, 0), 2)
    cv2.putText(img, f"Lenh: {current_command}", (10, 100), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
    
    cv2.imshow("Camera AI - Nhan dien cu chi", img)
    
    if cv2.waitKey(1) & 0xFF == 27: # Bấm phím ESC để thoát
        break

cap.release()
cv2.destroyAllWindows()