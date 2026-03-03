import cv2
import torch
import torch.nn as nn
import numpy as np

# ==============================
# CONFIG
# ==============================

IP_WEBCAM_URL = "http://192.168.0.109:8080/video"   # đổi IP
MODEL_PATH = "best_unet.pth"
INPUT_SIZE = 256  # đổi đúng size bạn train
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"

torch.backends.cudnn.benchmark = True

print("Using device:", DEVICE)

# ==============================
# MODEL DEFINITION (GIỐNG LÚC TRAIN)
# ==============================

class ResidualBlock(nn.Module):
    def __init__(self, in_ch, out_ch):
        super().__init__()
        self.conv = nn.Sequential(
            nn.Conv2d(in_ch, out_ch, 3, padding=1),
            nn.BatchNorm2d(out_ch),
            nn.ReLU(inplace=True),
            nn.Conv2d(out_ch, out_ch, 3, padding=1),
            nn.BatchNorm2d(out_ch)
        )
        self.shortcut = nn.Sequential(
            nn.Conv2d(in_ch, out_ch, 1),
            nn.BatchNorm2d(out_ch)
        ) if in_ch != out_ch else nn.Identity()
        self.relu = nn.ReLU(inplace=True)

    def forward(self, x):
        return self.relu(self.conv(x) + self.shortcut(x))

class ResUNet(nn.Module):
    def __init__(self):
        super().__init__()
        self.enc1 = ResidualBlock(3, 16)
        self.enc2 = ResidualBlock(16, 32)
        self.enc3 = ResidualBlock(32, 64)
        self.bottleneck = ResidualBlock(64, 128)

        self.up1 = nn.ConvTranspose2d(128, 64, 2, stride=2)
        self.dec1 = ResidualBlock(128, 64)

        self.up2 = nn.ConvTranspose2d(64, 32, 2, stride=2)
        self.dec2 = ResidualBlock(64, 32)

        self.up3 = nn.ConvTranspose2d(32, 16, 2, stride=2)
        self.dec3 = ResidualBlock(32, 16)

        self.out = nn.Conv2d(16, 1, 1)

    def forward(self, x):
        s1 = self.enc1(x)
        s2 = self.enc2(nn.MaxPool2d(2)(s1))
        s3 = self.enc3(nn.MaxPool2d(2)(s2))

        b = self.bottleneck(nn.MaxPool2d(2)(s3))

        d1 = self.dec1(torch.cat([self.up1(b), s3], dim=1))
        d2 = self.dec2(torch.cat([self.up2(d1), s2], dim=1))
        d3 = self.dec3(torch.cat([self.up3(d2), s1], dim=1))

        return self.out(d3)

# ==============================
# LOAD MODEL
# ==============================

model = ResUNet().to(DEVICE)
model.load_state_dict(torch.load(MODEL_PATH, map_location=DEVICE))
model.eval()

print("Model loaded!")

# ==============================
# CONNECT CAMERA
# ==============================

cap = cv2.VideoCapture(IP_WEBCAM_URL)

if not cap.isOpened():
    print("❌ Cannot connect camera")
    exit()

print("✅ Camera connected!")

# ==============================
# REALTIME LOOP
# ==============================

while True:
    ret, frame = cap.read()
    if not ret:
        break

    orig_h, orig_w = frame.shape[:2]

    # Resize đúng size train
    img = cv2.resize(frame, (INPUT_SIZE, INPUT_SIZE))
    img = img.astype(np.float32) / 255.0

    # Nếu bạn có normalize mean/std lúc train thì thêm vào đây

    img = np.transpose(img, (2, 0, 1))
    img = np.expand_dims(img, 0)
    img = torch.tensor(img).to(DEVICE)

    with torch.no_grad():
        output = model(img)
        output = torch.sigmoid(output)
        mask = output.squeeze().cpu().numpy()

    mask = (mask > 0.5).astype(np.uint8) * 255
    mask = cv2.resize(mask, (orig_w, orig_h))

    # Tạo overlay màu xanh
    colored_mask = np.zeros_like(frame)
    colored_mask[:, :, 1] = mask

    overlay = cv2.addWeighted(frame, 0.7, colored_mask, 0.3, 0)

    cv2.imshow("ResUNet Realtime", overlay)

    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

cap.release()
cv2.destroyAllWindows()