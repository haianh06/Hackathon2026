import React, { useState, useEffect, useRef, useCallback } from 'react';
import socket from '../services/socket';
import { getHardwareStatus, startHardwareDaemon, stopHardwareDaemon, getOrders, getDeliveryLogByOrder } from '../services/api';
import {
    ArrowUpIcon, ArrowDownIcon, ArrowLeftIcon, ArrowRightIcon,
    StopIcon, CpuChipIcon, SignalIcon, SignalSlashIcon
} from '@heroicons/react/24/solid';
import { VideoCameraIcon, MapPinIcon, CameraIcon, ClipboardDocumentListIcon, ChevronDownIcon, ChevronUpIcon } from '@heroicons/react/24/outline';

const MIN_CMD_DURATION = 300;
const STREAM_URL = '/camera/stream';
const SNAPSHOT_URL = '/camera/snapshot';

export default function StaffControlPage() {
    // Motor control state
    const [activeCmd, setActiveCmd] = useState(null);
    const [motorStatus, setMotorStatus] = useState(null);
    const [hwStatus, setHwStatus] = useState(null);
    const cmdStartTime = useRef(0);
    const stopTimer = useRef(null);

    // Camera state
    const [cameraOn, setCameraOn] = useState(false);

    // Navigation log state
    const [navLogs, setNavLogs] = useState([]);
    const logEndRef = useRef(null);

    // Delivery log per order state
    const [orders, setOrders] = useState([]);
    const [expandedOrderLog, setExpandedOrderLog] = useState(null);
    const [orderDeliveryLog, setOrderDeliveryLog] = useState(null);
    const [loadingLog, setLoadingLog] = useState(false);

    // Screenshot state
    const [screenshotting, setScreenshotting] = useState(false);
    const [screenshotCount, setScreenshotCount] = useState(0);
    const [lastScreenshotMsg, setLastScreenshotMsg] = useState('');
    const cameraImgRef = useRef(null);

    // ── Hardware status & socket listeners ──
    useEffect(() => {
        loadHwStatus();
        socket.on('motor-status-update', setMotorStatus);
        socket.on('hardware-status-update', setHwStatus);

        const handleNavLog = (data) => {
            const ts = data.timestamp ? new Date(data.timestamp * 1000).toLocaleTimeString('vi-VN') : '';
            let msg = '';

            if (data.type === 'start') {
                msg = `🚀 [${ts}] Bắt đầu — Tuyến: ${(data.route || []).join(' → ')}`;
            } else if (data.type === 'moving') {
                msg = `📍 [${ts}] ${data.fromPoint} → ${data.toPoint}  (${data.x}, ${data.y})  ${data.progress}%`;
            } else if (data.type === 'waypoint') {
                msg = `✅ [${ts}] Đã đến ${data.pointId} (${data.x}, ${data.y})`;
            } else if (data.type === 'complete') {
                msg = `🏁 [${ts}] HOÀN THÀNH — ${data.pointId} | ${(data.duration || 0).toFixed(1)}s`;
            } else if (data.type === 'cancelled') {
                msg = `⏹ [${ts}] Đã dừng`;
            } else if (data.type === 'line-correct') {
                const dir = data.correction < 0 ? '◀' : '▶';
                msg = `🔧 [${ts}] Bám làn: ${dir} ${data.correction > 0 ? '+' : ''}${data.correction?.toFixed(3)} (${data.steerTime}s)`;
            }

            if (msg) {
                setNavLogs(prev => {
                    const next = [...prev, msg];
                    return next.length > 200 ? next.slice(-150) : next;
                });
            }
        };

        socket.on('navigation-log', handleNavLog);

        return () => {
            socket.off('motor-status-update');
            socket.off('hardware-status-update');
            socket.off('navigation-log', handleNavLog);
            if (stopTimer.current) clearTimeout(stopTimer.current);
        };
    }, []);

    // Auto-scroll logs
    useEffect(() => {
        if (logEndRef.current) logEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }, [navLogs]);

    const loadHwStatus = async () => {
        try {
            const res = await getHardwareStatus();
            setHwStatus(res.data.data);
        } catch (e) { console.error(e); }
    };

    // Load orders for delivery log viewer
    useEffect(() => {
        const loadOrders = async () => {
            try {
                const res = await getOrders();
                setOrders(res.data.data || []);
            } catch (e) { console.error(e); }
        };
        loadOrders();

        socket.on('order-confirmed', loadOrders);
        socket.on('order-delivered', loadOrders);
        socket.on('order-arrived', loadOrders);
        socket.on('new-order', loadOrders);

        return () => {
            socket.off('order-confirmed', loadOrders);
            socket.off('order-delivered', loadOrders);
            socket.off('order-arrived', loadOrders);
            socket.off('new-order', loadOrders);
        };
    }, []);

    // Screenshot handler: capture from the live camera stream
    const handleScreenshot = useCallback(async () => {
        setScreenshotting(true);
        setLastScreenshotMsg('');
        const timestamp = new Date().toISOString().replace(/[:.]/g, '-');
        const filename = `camera-capture-${timestamp}.jpg`;

        let capturedViaCanvas = false;

        // Method 1: Try to capture from the displayed <img> element via canvas
        try {
            const img = cameraImgRef.current;
            if (img && img.naturalWidth > 0 && img.naturalHeight > 0) {
                const canvas = document.createElement('canvas');
                canvas.width = img.naturalWidth;
                canvas.height = img.naturalHeight;
                const ctx = canvas.getContext('2d');
                ctx.drawImage(img, 0, 0);

                // toDataURL will throw if canvas is tainted by CORS
                const dataUrl = canvas.toDataURL('image/jpeg', 0.95);
                const a = document.createElement('a');
                a.href = dataUrl;
                a.download = filename;
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                capturedViaCanvas = true;
                setScreenshotCount(prev => prev + 1);
                setLastScreenshotMsg(`✅ Đã lưu: ${filename}`);
            }
        } catch (canvasErr) {
            console.warn('Canvas capture failed, trying snapshot endpoint...', canvasErr);
        }

        // Method 2: Fallback to snapshot endpoint
        if (!capturedViaCanvas) {
            try {
                const response = await fetch(SNAPSHOT_URL);
                if (!response.ok) throw new Error('Snapshot endpoint error');
                const blob = await response.blob();
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = filename;
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                URL.revokeObjectURL(url);
                setScreenshotCount(prev => prev + 1);
                setLastScreenshotMsg(`✅ Đã lưu: ${filename}`);
            } catch (e) {
                console.error('Screenshot error:', e);
                setLastScreenshotMsg('❌ Không thể chụp. Kiểm tra camera.');
            }
        }

        setScreenshotting(false);
    }, []);

    // Toggle delivery log for an order
    const toggleOrderLog = useCallback(async (orderId) => {
        if (expandedOrderLog === orderId) {
            setExpandedOrderLog(null);
            setOrderDeliveryLog(null);
            return;
        }
        setExpandedOrderLog(orderId);
        setLoadingLog(true);
        try {
            const res = await getDeliveryLogByOrder(orderId);
            setOrderDeliveryLog(res.data.data);
        } catch (e) {
            setOrderDeliveryLog(null);
        } finally {
            setLoadingLog(false);
        }
    }, [expandedOrderLog]);

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

    // Keyboard controls
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
            ? 'bg-amber-500 text-white shadow-lg scale-95'
            : 'bg-white text-gray-700 border border-gray-200 hover:bg-amber-50 hover:border-amber-300 shadow-sm'
        }`;

    return (
        <div className="max-w-6xl mx-auto">
            <h1 className="text-2xl font-bold text-gray-900 mb-6">Điều khiển xe & Camera</h1>

            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">

                {/* ── Column 1-2: Camera + Log ── */}
                <div className="lg:col-span-2 space-y-4">
                    {/* Camera panel */}
                    <div className="bg-white rounded-2xl border border-gray-200 overflow-hidden">
                        <div className="flex items-center justify-between px-5 py-3 border-b bg-gray-50">
                            <h2 className="text-sm font-semibold text-gray-600 flex items-center gap-1.5">
                                <VideoCameraIcon className="w-4 h-4" /> Camera MJPEG
                            </h2>
                            <div className="flex items-center gap-2">
                                <span className={`w-2 h-2 rounded-full ${cameraOn ? 'bg-green-500 animate-pulse' : 'bg-gray-300'}`} />
                                {cameraOn && (
                                    <button onClick={handleScreenshot}
                                        disabled={screenshotting}
                                        className="px-3 py-1.5 text-xs font-medium rounded-lg transition bg-blue-500 text-white hover:bg-blue-600 disabled:opacity-50 inline-flex items-center gap-1">
                                        <CameraIcon className="w-3.5 h-3.5" />
                                        {screenshotting ? 'Đang chụp...' : 'Chụp ảnh'}
                                    </button>
                                )}
                                <button onClick={() => setCameraOn(!cameraOn)}
                                    className={`px-3 py-1.5 text-xs font-medium rounded-lg transition ${cameraOn
                                        ? 'bg-red-500 text-white hover:bg-red-600'
                                        : 'bg-amber-500 text-white hover:bg-amber-600'
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
                        {/* Screenshot toolbar - always visible when camera is on */}
                        {cameraOn && (
                            <div className="px-5 py-3 border-t bg-gray-50 flex items-center justify-between">
                                <div className="flex items-center gap-3">
                                    <button
                                        onClick={handleScreenshot}
                                        disabled={screenshotting}
                                        className="inline-flex items-center gap-2 px-4 py-2 bg-blue-600 text-white text-sm font-semibold rounded-lg hover:bg-blue-700 active:scale-95 disabled:opacity-50 transition-all shadow-sm"
                                    >
                                        <CameraIcon className="w-5 h-5" />
                                        {screenshotting ? 'Đang chụp...' : '📸 Chụp ảnh'}
                                    </button>
                                    <span className="text-xs text-gray-400">
                                        Đã chụp: <span className="font-bold text-gray-600">{screenshotCount}</span> ảnh
                                        <span className="ml-2 text-gray-300">|</span>
                                        <span className="ml-2">Nhấn <kbd className="px-1.5 py-0.5 bg-gray-200 rounded text-[10px] font-mono font-bold text-gray-600">Enter</kbd> để chụp nhanh</span>
                                    </span>
                                </div>
                                {lastScreenshotMsg && (
                                    <span className={`text-xs font-medium ${lastScreenshotMsg.startsWith('✅') ? 'text-green-600' : 'text-red-500'}`}>
                                        {lastScreenshotMsg}
                                    </span>
                                )}
                            </div>
                        )}
                    </div>

                    {/* Navigation log panel */}
                    <div className="bg-white rounded-2xl border border-gray-200 overflow-hidden flex flex-col" style={{ maxHeight: 320 }}>
                        <div className="flex items-center justify-between px-5 py-3 border-b bg-gray-50">
                            <h2 className="text-sm font-semibold text-gray-600 flex items-center gap-1.5">
                                <MapPinIcon className="w-4 h-4" /> Log di chuyển
                            </h2>
                            <button onClick={() => setNavLogs([])}
                                className="text-xs text-gray-400 hover:text-red-500 transition">
                                Xoá log
                            </button>
                        </div>
                        <div className="flex-1 overflow-y-auto p-4 font-mono text-xs leading-relaxed bg-gray-900 text-green-400">
                            {navLogs.length === 0 && (
                                <div className="text-gray-600 text-center py-8">
                                    Chờ dữ liệu điều hướng...
                                </div>
                            )}
                            {navLogs.map((log, idx) => (
                                <div key={idx} className={`py-0.5 ${log.startsWith('🏁') ? 'text-yellow-300 font-bold' :
                                    log.startsWith('⏹') ? 'text-red-400' :
                                        log.startsWith('✅') ? 'text-green-300' : ''
                                    }`}>
                                    {log}
                                </div>
                            ))}
                            <div ref={logEndRef} />
                        </div>
                    </div>

                    {/* Delivery Logs per Order panel */}
                    <div className="bg-white rounded-2xl border border-gray-200 overflow-hidden">
                        <div className="flex items-center justify-between px-5 py-3 border-b bg-gray-50">
                            <h2 className="text-sm font-semibold text-gray-600 flex items-center gap-1.5">
                                <ClipboardDocumentListIcon className="w-4 h-4" /> Log giao hàng theo đơn
                            </h2>
                        </div>
                        <div className="max-h-80 overflow-y-auto divide-y divide-gray-100">
                            {orders.filter(o => ['confirmed', 'delivering', 'arrived', 'delivered'].includes(o.status)).length === 0 ? (
                                <div className="text-center py-6 text-sm text-gray-400">Chưa có đơn hàng nào đang giao</div>
                            ) : (
                                orders.filter(o => ['confirmed', 'delivering', 'arrived', 'delivered'].includes(o.status)).slice(0, 20).map(order => {
                                    const isExpanded = expandedOrderLog === order._id;
                                    const statusLabels = {
                                        confirmed: 'Đã xác nhận',
                                        delivering: 'Đang giao',
                                        arrived: 'Đã đến nơi',
                                        delivered: 'Hoàn thành'
                                    };
                                    const statusColors = {
                                        confirmed: 'text-blue-600 bg-blue-50',
                                        delivering: 'text-amber-600 bg-amber-50',
                                        arrived: 'text-green-600 bg-green-50',
                                        delivered: 'text-green-700 bg-green-100'
                                    };
                                    return (
                                        <div key={order._id}>
                                            <button
                                                onClick={() => toggleOrderLog(order._id)}
                                                className="w-full flex items-center justify-between px-5 py-3 hover:bg-gray-50 transition text-left"
                                            >
                                                <div className="flex items-center gap-3 min-w-0">
                                                    <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${statusColors[order.status] || ''}`}>
                                                        {statusLabels[order.status] || order.status}
                                                    </span>
                                                    <span className="font-mono text-xs text-gray-500">#{order._id.slice(-6).toUpperCase()}</span>
                                                    <span className="text-xs text-gray-400 truncate">→ {order.destinationPoint}</span>
                                                </div>
                                                {isExpanded ? <ChevronUpIcon className="w-4 h-4 text-gray-400" /> : <ChevronDownIcon className="w-4 h-4 text-gray-400" />}
                                            </button>
                                            {isExpanded && (
                                                <div className="px-5 pb-4 bg-gray-50">
                                                    {loadingLog ? (
                                                        <div className="text-center py-3">
                                                            <div className="w-5 h-5 border-2 border-amber-500 border-t-transparent rounded-full animate-spin mx-auto" />
                                                        </div>
                                                    ) : orderDeliveryLog ? (
                                                        <div className="space-y-2 text-sm">
                                                            <div className="grid grid-cols-2 gap-2">
                                                                <div>
                                                                    <span className="text-gray-400 text-xs">Nhân viên</span>
                                                                    <p className="font-medium text-gray-700">{orderDeliveryLog.staff?.displayName || 'N/A'}</p>
                                                                </div>
                                                                <div>
                                                                    <span className="text-gray-400 text-xs">Khách hàng</span>
                                                                    <p className="font-medium text-gray-700">{orderDeliveryLog.customer?.displayName || 'N/A'}</p>
                                                                </div>
                                                                <div>
                                                                    <span className="text-gray-400 text-xs">Điểm đến</span>
                                                                    <p className="font-medium text-gray-700">{orderDeliveryLog.destinationPoint}</p>
                                                                </div>
                                                                <div>
                                                                    <span className="text-gray-400 text-xs">Trạng thái</span>
                                                                    <p className={`font-medium ${orderDeliveryLog.status === 'completed' ? 'text-green-600' : orderDeliveryLog.status === 'in-progress' ? 'text-amber-600' : orderDeliveryLog.status === 'failed' ? 'text-red-600' : 'text-gray-600'}`}>
                                                                        {orderDeliveryLog.status === 'completed' ? '✅ Hoàn thành' : orderDeliveryLog.status === 'in-progress' ? '🚚 Đang giao' : orderDeliveryLog.status === 'failed' ? '❌ Thất bại' : '⏳ Chờ'}
                                                                    </p>
                                                                </div>
                                                                {orderDeliveryLog.startAt && (
                                                                    <div>
                                                                        <span className="text-gray-400 text-xs">Bắt đầu</span>
                                                                        <p className="font-medium text-gray-700">{new Date(orderDeliveryLog.startAt).toLocaleString('vi-VN')}</p>
                                                                    </div>
                                                                )}
                                                                {orderDeliveryLog.endAt && (
                                                                    <div>
                                                                        <span className="text-gray-400 text-xs">Kết thúc</span>
                                                                        <p className="font-medium text-gray-700">{new Date(orderDeliveryLog.endAt).toLocaleString('vi-VN')}</p>
                                                                    </div>
                                                                )}
                                                                {orderDeliveryLog.timeProcess != null && (
                                                                    <div className="col-span-2">
                                                                        <span className="text-gray-400 text-xs">Thời gian giao</span>
                                                                        <p className="font-medium text-amber-600">{orderDeliveryLog.timeProcess}s ({(orderDeliveryLog.timeProcess / 60).toFixed(1)} phút)</p>
                                                                    </div>
                                                                )}
                                                            </div>
                                                        </div>
                                                    ) : (
                                                        <p className="text-sm text-gray-400 py-2">Chưa có log giao hàng cho đơn này</p>
                                                    )}
                                                </div>
                                            )}
                                        </div>
                                    );
                                })
                            )}
                        </div>
                    </div>
                </div>

                {/* ── Column 3: Controls + Hardware info ── */}
                <div className="space-y-4">
                    {/* D-pad */}
                    <div className="bg-white rounded-2xl border border-gray-200 p-6 flex flex-col items-center">
                        <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wide mb-4">Bàn điều khiển</h2>

                        <div className="grid grid-rows-3 gap-2">
                            {/* Row 1: Forward */}
                            <div className="flex justify-center">
                                <button className={btnClass('forward')}
                                    onPointerDown={handlePointerDown('forward')}
                                    onPointerUp={handlePointerUp}
                                    onPointerLeave={handlePointerUp}>
                                    <ArrowUpIcon className="w-7 h-7" />
                                </button>
                            </div>
                            {/* Row 2: Left | Stop | Right */}
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
                            {/* Row 3: Backward */}
                            <div className="flex justify-center">
                                <button className={btnClass('backward')}
                                    onPointerDown={handlePointerDown('backward')}
                                    onPointerUp={handlePointerUp}
                                    onPointerLeave={handlePointerUp}>
                                    <ArrowDownIcon className="w-7 h-7" />
                                </button>
                            </div>
                        </div>

                        <p className="text-xs text-gray-400 mt-4">W/A/S/D hoặc Arrow keys &middot; Space = Dừng &middot; Enter = Chụp ảnh</p>
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
                                    <span className="text-gray-500">Trạng thái</span>
                                    <span className={`font-medium ${motorStatus.connected ? 'text-green-600' : 'text-red-500'}`}>
                                        {motorStatus.connected ? 'Kết nối' : 'Ngắt'}
                                    </span>
                                </div>
                                {motorStatus.port && (
                                    <div className="flex justify-between">
                                        <span className="text-gray-500">Port</span>
                                        <span className="font-mono text-xs">{motorStatus.port}</span>
                                    </div>
                                )}
                            </div>
                        ) : (
                            <p className="text-gray-400 text-sm">Chưa có dữ liệu</p>
                        )}
                    </div>

                    {/* Daemon control */}
                    <div className="bg-white rounded-2xl border border-gray-200 p-5">
                        <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wide mb-3">Hardware Daemon</h2>
                        {hwStatus ? (
                            <div className="space-y-2 text-sm mb-4">
                                <div className="flex justify-between items-center">
                                    <span className="text-gray-500">Daemon</span>
                                    <span className={`inline-flex items-center gap-1 font-medium ${hwStatus.daemon_running ? 'text-green-600' : 'text-red-500'}`}>
                                        {hwStatus.daemon_running ? <SignalIcon className="w-4 h-4" /> : <SignalSlashIcon className="w-4 h-4" />}
                                        {hwStatus.daemon_running ? 'Running' : 'Stopped'}
                                    </span>
                                </div>
                                <div className="flex justify-between">
                                    <span className="text-gray-500">Line-Follow</span>
                                    <span className="font-medium">{hwStatus.line_follower ? 'ResUNet' : 'Off'}</span>
                                </div>
                                <div className="flex justify-between">
                                    <span className="text-gray-500">Camera</span>
                                    <span className="font-medium">{hwStatus.camera?.camera_active ? 'Active' : 'Off'}</span>
                                </div>
                                <div className="flex justify-between">
                                    <span className="text-gray-500">Platform</span>
                                    <span className="font-mono text-xs">{hwStatus.platform || '?'}</span>
                                </div>
                            </div>
                        ) : <p className="text-gray-400 text-sm mb-4">Chưa có dữ liệu</p>}

                        <div className="flex gap-2">
                            <button onClick={async () => { await startHardwareDaemon(); loadHwStatus(); }}
                                className="flex-1 px-3 py-2 bg-green-500 text-white text-sm rounded-lg hover:bg-green-600 transition">
                                Start
                            </button>
                            <button onClick={async () => { await stopHardwareDaemon(); loadHwStatus(); }}
                                className="flex-1 px-3 py-2 bg-red-500 text-white text-sm rounded-lg hover:bg-red-600 transition">
                                Stop
                            </button>
                            <button onClick={loadHwStatus}
                                className="px-3 py-2 bg-gray-100 text-gray-600 text-sm rounded-lg hover:bg-gray-200 transition">
                                Refresh
                            </button>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    );
}
