echo "📷 Starting camera stream (rpicam-vid) on host..."
# Kill any existing rpicam-vid
pkill -f 'rpicam-vid.*tcp://' 2>/dev/null || true
sleep 1

if command -v rpicam-vid &>/dev/null; then
    # --quality 90  : JPEG quality (default ~50 rất mờ)
    # --sharpness 1.5: tăng nét ISP
    # 1280x720 @15fps: cân bằng nét vs bandwidth
    rpicam-vid --codec mjpeg -t 0 --nopreview \
        --width 1280 --height 720 --framerate 15 \
        --quality 90 --sharpness 1.5 \
        --listen -o tcp://0.0.0.0:8554 &
        
    echo $! > /tmp/rpicam-vid.pid
    echo "   ✅ rpicam-vid started (PID $(cat /tmp/rpicam-vid.pid)), TCP port 8554"
    sleep 2
else
    echo "   ⚠️  rpicam-vid not found — camera will not be available"
fi