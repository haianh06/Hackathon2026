import React, { useState, useEffect, useRef, useCallback } from 'react';
import socket from '../services/socket';
import { getHardwareStatus } from '../services/api';
import {
    ArrowUpIcon, ArrowDownIcon, ArrowLeftIcon, ArrowRightIcon,
    StopIcon, CpuChipIcon,
} from '@heroicons/react/24/solid';
import { VideoCameraIcon, CameraIcon, EyeIcon } from '@heroicons/react/24/outline';

const MIN_CMD_DURATION = 300;
const STREAM_URL = '/camera/stream';
const SNAPSHOT_URL = '/camera/snapshot';

export default function AdminDetectPage() {
    // Motor control
    const [activeCmd, setActiveCmd] = useState(null);
    const [motorStatus, setMotorStatus] = useState(null);
    const [hwStatus, setHwStatus] = useState(null);
    const cmdStartTime = useRef(0);
    const stopTimer = useRef(null);

    // Camera
    const [cameraOn, setCameraOn] = useState(false);
    const cameraImgRef = useRef(null);

    // Screenshot
    const [screenshotting, setScreenshotting] = useState(false);
    const [screenshotCount, setScreenshotCount] = useState(0);
    const [lastScreenshotMsg, setLastScreenshotMsg] = useState('');

    // Object detection
    const [detecting, setDetecting] = useState(false);
    const [detections, setDetections] = useState([]);
    const [detectStatus, setDetectStatus] = useState(null);

    // Color target
    const [targetValue, setTargetValue] = useState(128);
    const [threshold, setThreshold] = useState(30);

    // ── Socket listeners ──
    useEffect(() => {
        loadHwStatus();
        socket.on('motor-status-update', setMotorStatus);
        socket.on('hardware-status-update', setHwStatus);

        // Object detection events
        socket.on('object-detect-status', (data) => {
            setDetecting(data.detecting || false);
            setDetectStatus(data);
        });
        socket.on('object-detect-result', (data) => {
            setDetections(data.detections || []);
        });

        return () => {
            socket.off('motor-status-update');
            socket.off('hardware-status-update');
            socket.off('object-detect-status');
            socket.off('object-detect-result');
            if (stopTimer.current) clearTimeout(stopTimer.current);
        };
    }, []);

    const loadHwStatus = async () => {
        try {
            const res = await getHardwareStatus();
            setHwStatus(res.data.data);
        } catch (e) { console.error(e); }
    };

    // ── Motor commands ──
    const sendCommand = useCallback((command) => {
        if (stopTimer.current) { clearTimeout(stopTimer.current); stopTimer.current = null; }
        socket.emit('motor-control', { command, speed: 50 });
        setActiveCmd(command);
        cmdStartTime.current = Date.now();
    }, []);

    const stopMotor = useCallback(() => {
        const elapsed = Date.now() - cmdStartTime.current;
        if (elapsed < MIN_CMD_DURATION) {
            if (stopTimer.current) clearTimeout(stopTimer.current);
            stopTimer.current = setTimeout(() => {
                socket.emit('motor-control', { command: 'stop', speed: 0 });
                setActiveCmd(null);
                stopTimer.current = null;
            }, MIN_CMD_DURATION - elapsed);
        } else {
            socket.emit('motor-control', { command: 'stop', speed: 0 });
            setActiveCmd(null);
        }
    }, []);

    const handlePointerDown = useCallback((cmd) => (e) => { e.preventDefault(); sendCommand(cmd); }, [sendCommand]);
    const handlePointerUp = useCallback((e) => { e.preventDefault(); stopMotor(); }, [stopMotor]);

    // ── Detection controls ──
    const setTarget = () => socket.emit('object-detect-set-target', { target: targetValue, threshold });
    const startDetection = () => socket.emit('object-detect-start');
    const stopDetection = () => socket.emit('object-detect-stop');
    const detectOnce = () => socket.emit('object-detect-once');

    // Screenshot
    const handleScreenshot = useCallback(async () => {
        setScreenshotting(true);
        setLastScreenshotMsg('');
        const timestamp = new Date().toISOString().replace(/[:.]/g, '-');
        const filename = `detect-capture-${timestamp}.jpg`;

        let ok = false;
        try {
            const img = cameraImgRef.current;
            if (img && img.naturalWidth > 0) {
                const canvas = document.createElement('canvas');
                canvas.width = img.naturalWidth;
                canvas.height = img.naturalHeight;
                canvas.getContext('2d').drawImage(img, 0, 0);
                const a = document.createElement('a');
                a.href = canvas.toDataURL('image/jpeg', 0.95);
                a.download = filename;
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                ok = true;
            }
        } catch (_) { /* fallback below */ }

        if (!ok) {
            try {
                const res = await fetch(SNAPSHOT_URL);
                if (!res.ok) throw new Error();
                const blob = await res.blob();
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = filename;
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                URL.revokeObjectURL(url);
                ok = true;
            } catch (_) { /* ignore */ }
        }

        setScreenshotCount(c => c + (ok ? 1 : 0));
        setLastScreenshotMsg(ok ? `✅ Đã lưu: ${filename}` : '❌ Không thể chụp');
        setScreenshotting(false);
    }, []);

    // Keyboard
    useEffect(() => {
        const down = (e) => {
            if (e.repeat) return;
            switch (e.key.toLowerCase()) {
                case 'w': case 'arrowup': sendCommand('forward'); break;
                case 's': case 'arrowdown': sendCommand('backward'); break;
                case 'a': case 'arrowleft': sendCommand('left'); break;
                case 'd': case 'arrowright': sendCommand('right'); break;
                case ' ': e.preventDefault(); stopMotor(); break;
                case 'enter': e.preventDefault(); if (cameraOn) handleScreenshot(); break;
                default: break;
            }
        };
        const up = (e) => {
            const keys = ['w', 's', 'a', 'd', 'arrowup', 'arrowdown', 'arrowleft', 'arrowright'];
            if (keys.includes(e.key.toLowerCase())) stopMotor();
        };
        window.addEventListener('keydown', down);
        window.addEventListener('keyup', up);
        return () => { window.removeEventListener('keydown', down); window.removeEventListener('keyup', up); };
    }, [sendCommand, stopMotor, cameraOn, handleScreenshot]);

    const btnClass = (cmd) =>
        `w-16 h-16 rounded-xl flex items-center justify-center transition-all duration-100 select-none ${activeCmd === cmd
            ? 'bg-indigo-500 text-white shadow-lg scale-95'
            : 'bg-white text-gray-700 border border-gray-200 hover:bg-indigo-50 hover:border-indigo-300 shadow-sm'
        }`;

    return (
        <div className="max-w-6xl mx-auto">
            <h1 className="text-2xl font-bold text-gray-900 mb-6">Điều khiển & Nhận diện vật thể</h1>

            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">

                {/* ── Column 1-2: Camera + Detections ── */}
                <div className="lg:col-span-2 space-y-4">
                    {/* Camera */}
                    <div className="bg-white rounded-2xl border border-gray-200 overflow-hidden">
                        <div className="flex items-center justify-between px-5 py-3 border-b bg-gray-50">
                            <h2 className="text-sm font-semibold text-gray-600 flex items-center gap-1.5">
                                <VideoCameraIcon className="w-4 h-4" /> Camera MJPEG
                            </h2>
                            <div className="flex items-center gap-2">
                                <span className={`w-2 h-2 rounded-full ${cameraOn ? 'bg-green-500 animate-pulse' : 'bg-gray-300'}`} />
                                {cameraOn && (
                                    <button onClick={handleScreenshot} disabled={screenshotting}
                                        className="px-3 py-1.5 text-xs font-medium rounded-lg bg-blue-500 text-white hover:bg-blue-600 disabled:opacity-50 inline-flex items-center gap-1">
                                        <CameraIcon className="w-3.5 h-3.5" />
                                        {screenshotting ? 'Đang chụp...' : 'Chụp ảnh'}
                                    </button>
                                )}
                                <button onClick={() => setCameraOn(!cameraOn)}
                                    className={`px-3 py-1.5 text-xs font-medium rounded-lg transition ${cameraOn
                                        ? 'bg-red-500 text-white hover:bg-red-600'
                                        : 'bg-indigo-500 text-white hover:bg-indigo-600'
                                        }`}>
                                    {cameraOn ? 'Ngắt' : 'Kết nối'}
                                </button>
                            </div>
                        </div>
                        <div className="aspect-video bg-gray-900 flex items-center justify-center relative">
                            {cameraOn ? (
                                <img ref={cameraImgRef} src={STREAM_URL} alt="Camera"
                                    className="w-full h-full object-contain"
                                    crossOrigin="anonymous"
                                    onError={() => setCameraOn(false)} />
                            ) : (
                                <div className="text-gray-500 text-sm text-center">
                                    <VideoCameraIcon className="w-10 h-10 mx-auto mb-2 opacity-40" />
                                    Nhấn "Kết nối" để xem camera
                                </div>
                            )}
                        </div>
                        {cameraOn && (
                            <div className="px-5 py-3 border-t bg-gray-50 flex items-center justify-between">
                                <span className="text-xs text-gray-400">
                                    Đã chụp: <span className="font-bold text-gray-600">{screenshotCount}</span> ảnh
                                </span>
                                {lastScreenshotMsg && (
                                    <span className={`text-xs font-medium ${lastScreenshotMsg.startsWith('✅') ? 'text-green-600' : 'text-red-500'}`}>
                                        {lastScreenshotMsg}
                                    </span>
                                )}
                            </div>
                        )}
                    </div>

                    {/* Detection results */}
                    <div className="bg-white rounded-2xl border border-gray-200 overflow-hidden">
                        <div className="flex items-center justify-between px-5 py-3 border-b bg-gray-50">
                            <h2 className="text-sm font-semibold text-gray-600 flex items-center gap-1.5">
                                <EyeIcon className="w-4 h-4" /> Nhận diện màu
                            </h2>
                            <div className="flex items-center gap-2">
                                <span className={`inline-flex items-center gap-1 text-xs font-medium px-2 py-1 rounded-full ${detecting
                                    ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-500'
                                    }`}>
                                    {detecting ? '● Đang detect' : '○ Tắt'}
                                </span>
                                {!detecting ? (
                                    <button onClick={startDetection}
                                        className="px-3 py-1.5 text-xs font-medium rounded-lg bg-green-500 text-white hover:bg-green-600">
                                        Bật Detect
                                    </button>
                                ) : (
                                    <button onClick={stopDetection}
                                        className="px-3 py-1.5 text-xs font-medium rounded-lg bg-red-500 text-white hover:bg-red-600">
                                        Tắt Detect
                                    </button>
                                )}
                                <button onClick={detectOnce}
                                    className="px-3 py-1.5 text-xs font-medium rounded-lg bg-indigo-500 text-white hover:bg-indigo-600">
                                    Detect 1 lần
                                </button>
                            </div>
                        </div>

                        {/* Target setting */}
                        <div className="px-5 py-3 border-b bg-blue-50">
                            <p className="text-xs font-semibold text-blue-700 mb-2">Thiết lập Target màu (mean grayscale)</p>
                            <div className="flex items-center gap-3">
                                <div className="flex items-center gap-1.5">
                                    <label className="text-xs text-gray-500">Target:</label>
                                    <input type="number" value={targetValue}
                                        onChange={e => setTargetValue(Number(e.target.value))}
                                        className="w-20 px-2 py-1 text-sm border rounded-lg focus:ring-2 focus:ring-blue-400"
                                        min={0} max={255} />
                                </div>
                                <div className="flex items-center gap-1.5">
                                    <label className="text-xs text-gray-500">Threshold:</label>
                                    <input type="number" value={threshold}
                                        onChange={e => setThreshold(Number(e.target.value))}
                                        className="w-20 px-2 py-1 text-sm border rounded-lg focus:ring-2 focus:ring-blue-400"
                                        min={1} max={255} />
                                </div>
                                <button onClick={setTarget}
                                    className="px-3 py-1.5 text-xs font-medium rounded-lg bg-blue-500 text-white hover:bg-blue-600">
                                    Set Target
                                </button>
                                <div className="w-8 h-8 rounded border"
                                    style={{ backgroundColor: `rgb(${targetValue},${targetValue},${targetValue})` }}
                                    title={`Preview: ${targetValue}`} />
                            </div>
                        </div>

                        <div className="p-4 max-h-64 overflow-y-auto">
                            {detections.length === 0 ? (
                                <div className="text-center py-6 text-sm text-gray-400">
                                    {detecting ? 'Đang chờ kết quả...' : 'Chưa có kết quả. Set target rồi bật detect hoặc nhấn "Detect 1 lần"'}
                                </div>
                            ) : (
                                <div className="space-y-2">
                                    {detections.map((d, i) => (
                                        <div key={i} className={`flex items-center justify-between p-3 rounded-lg ${d.matched ? 'bg-green-50 border border-green-200' : 'bg-red-50 border border-red-200'}`}>
                                            <div className="flex items-center gap-3">
                                                <span className={`w-8 h-8 rounded-full flex items-center justify-center text-xs font-bold ${d.matched ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'}`}>
                                                    {d.matched ? '✓' : '✗'}
                                                </span>
                                                <div>
                                                    <p className="text-sm font-semibold text-gray-800">
                                                        {d.matched ? 'Khớp màu!' : 'Không khớp'}
                                                    </p>
                                                    <p className="text-xs text-gray-400">
                                                        Mean: {d.mean} | Target: {d.target} | Confidence: {((d.confidence || 0) * 100).toFixed(1)}%
                                                        {d.bbox && ` | ROI: [${d.bbox.map(v => Math.round(v)).join(', ')}]`}
                                                    </p>
                                                </div>
                                            </div>
                                        </div>
                                    ))}
                                </div>
                            )}
                        </div>
                        {detectStatus?.error && (
                            <div className="px-5 py-2 border-t bg-red-50 text-xs text-red-600">
                                ⚠ {detectStatus.error}
                            </div>
                        )}
                    </div>
                </div>

                {/* ── Column 3: Controls ── */}
                <div className="space-y-4">
                    {/* D-pad */}
                    <div className="bg-white rounded-2xl border border-gray-200 p-6 flex flex-col items-center">
                        <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wide mb-4">Bàn điều khiển</h2>
                        <div className="grid grid-rows-3 gap-2">
                            <div className="flex justify-center">
                                <button className={btnClass('forward')}
                                    onPointerDown={handlePointerDown('forward')}
                                    onPointerUp={handlePointerUp}
                                    onPointerLeave={handlePointerUp}>
                                    <ArrowUpIcon className="w-7 h-7" />
                                </button>
                            </div>
                            <div className="flex gap-2">
                                <button className={btnClass('left')}
                                    onPointerDown={handlePointerDown('left')}
                                    onPointerUp={handlePointerUp}
                                    onPointerLeave={handlePointerUp}>
                                    <ArrowLeftIcon className="w-7 h-7" />
                                </button>
                                <button
                                    className="w-16 h-16 rounded-xl flex items-center justify-center bg-red-500 text-white shadow-md hover:bg-red-600 active:scale-95 transition-all select-none"
                                    onClick={stopMotor}>
                                    <StopIcon className="w-7 h-7" />
                                </button>
                                <button className={btnClass('right')}
                                    onPointerDown={handlePointerDown('right')}
                                    onPointerUp={handlePointerUp}
                                    onPointerLeave={handlePointerUp}>
                                    <ArrowRightIcon className="w-7 h-7" />
                                </button>
                            </div>
                            <div className="flex justify-center">
                                <button className={btnClass('backward')}
                                    onPointerDown={handlePointerDown('backward')}
                                    onPointerUp={handlePointerUp}
                                    onPointerLeave={handlePointerUp}>
                                    <ArrowDownIcon className="w-7 h-7" />
                                </button>
                            </div>
                        </div>
                        <p className="text-xs text-gray-400 mt-4">W/A/S/D · Arrow keys · Space = Dừng</p>
                    </div>

                    {/* Motor status */}
                    <div className="bg-white rounded-2xl border border-gray-200 p-5">
                        <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wide mb-3 flex items-center gap-1.5">
                            <CpuChipIcon className="w-4 h-4" /> Motor
                        </h2>
                        {motorStatus ? (
                            <div className="space-y-2 text-sm">
                                <div className="flex justify-between">
                                    <span className="text-gray-500">Driver</span>
                                    <span className="font-medium">{motorStatus.driver}</span>
                                </div>
                                <div className="flex justify-between">
                                    <span className="text-gray-500">Status</span>
                                    <span className="font-medium">{motorStatus.status || 'idle'}</span>
                                </div>
                                <div className="flex justify-between">
                                    <span className="text-gray-500">GPIO</span>
                                    <span className={`font-medium ${motorStatus.gpio_connected ? 'text-green-600' : 'text-red-500'}`}>
                                        {motorStatus.gpio_connected ? 'Kết nối' : 'Ngắt'}
                                    </span>
                                </div>
                            </div>
                        ) : <p className="text-gray-400 text-sm">Chưa có dữ liệu</p>}
                    </div>

                    {/* Detect service status */}
                    <div className="bg-white rounded-2xl border border-gray-200 p-5">
                        <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wide mb-3 flex items-center gap-1.5">
                            <EyeIcon className="w-4 h-4" /> Color Detect
                        </h2>
                        <div className="space-y-2 text-sm">
                            <div className="flex justify-between">
                                <span className="text-gray-500">Trạng thái</span>
                                <span className={`font-medium ${detecting ? 'text-green-600' : 'text-gray-500'}`}>
                                    {detecting ? '● Đang chạy' : '○ Tắt'}
                                </span>
                            </div>
                            <div className="flex justify-between">
                                <span className="text-gray-500">Kết quả</span>
                                <span className={`font-bold ${detections.some(d => d.matched) ? 'text-green-600' : 'text-red-500'}`}>
                                    {detections.length > 0 ? (detections[0].matched ? '✓ Khớp' : '✗ Không khớp') : '—'}
                                </span>
                            </div>
                            {detectStatus?.target != null && (
                                <div className="flex justify-between">
                                    <span className="text-gray-500">Target</span>
                                    <span className="font-medium">{detectStatus.target}</span>
                                </div>
                            )}
                        </div>
                    </div>
                </div>
            </div>
        </div>
    );
}
