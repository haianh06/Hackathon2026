echo "📷 Starting camera stream (rpicam-vid) on host..."
# Kill any existing rpicam-vid
pkill -f 'rpicam-vid.*tcp://' 2>/dev/null || true
sleep 1

if command -v rpicam-vid &>/dev/null; then
    # Thêm --mode để ép cảm biến đọc ở độ phân giải cao (góc rộng)
    # ISP sẽ tự động thu nhỏ (scale down) xuống width 640 x height 480
    rpicam-vid --codec mjpeg -t 0 --nopreview \
        --mode 1640:1232 \
        --width 640 --height 480 --framerate 15 \
        --rotation 180 \
        --roi 0,0,1,1 \
        --listen -o tcp://0.0.0.0:8554 &
        
    echo $! > /tmp/rpicam-vid.pid
    echo "   ✅ rpicam-vid started (PID $(cat /tmp/rpicam-vid.pid)), TCP port 8554"
    sleep 2
else
    echo "   ⚠️  rpicam-vid not found — camera will not be available"
fi