import React, { useState, useEffect, useRef, useCallback } from 'react';
import socket from '../services/socket';
import {
    ArrowUpIcon, ArrowUturnLeftIcon, ArrowUturnRightIcon,
    StopIcon, MapPinIcon, CheckCircleIcon, XCircleIcon,
    TrashIcon, ArrowDownTrayIcon
} from '@heroicons/react/24/outline';
import { VideoCameraIcon, CameraIcon, EyeIcon } from '@heroicons/react/24/outline';

const PROCESSED_URL = '/camera/processed/stream';
const SNAPSHOT_URL = '/camera/snapshot';
const DEBUG_URL = '/camera/lane/debug';

// Direction arrows for the grid
const DIR_ARROWS = ['↑', '→', '↓', '←'];
const DIR_NAMES = ['Lên (+Y)', 'Phải (+X)', 'Xuống (-Y)', 'Trái (-X)'];

export default function MapBuilderPage() {
    // Position state
    const [position, setPosition] = useState({ x: 0, y: 0, direction: 0 });
    const [stepCount, setStepCount] = useState(0);

    // Approved points
    const [approvedPoints, setApprovedPoints] = useState([{ x: 0, y: 0, label: 'S', approved: true }]);
    const [pendingPoint, setPendingPoint] = useState(null);

    // Camera
    const [cameraOn, setCameraOn] = useState(false);
    const [cameraMode, setCameraMode] = useState('raw'); // 'raw' | 'canny' | 'unet' | 'sign' | 'all'
    const cameraImgRef = useRef(null);

    // Screenshot
    const [screenshotCount, setScreenshotCount] = useState(0);

    // Analysis
    const [analysis, setAnalysis] = useState(null);

    // Logs
    const [logs, setLogs] = useState([`[START] Xe ở vị trí (0, 0) — Hướng: ${DIR_NAMES[0]}`]);
    const logEndRef = useRef(null);

    // Step config
    const [stepDuration, setStepDuration] = useState(0.6);
    const [stepSpeed, setStepSpeed] = useState(40);

    // Moving state
    const [isMoving, setIsMoving] = useState(false);

    // Point label input
    const [labelInput, setLabelInput] = useState('');

    // Road sign detection
    const [signDetecting, setSignDetecting] = useState(false);
    const [signDetections, setSignDetections] = useState([]);

    const addLog = useCallback((msg) => {
        const ts = new Date().toLocaleTimeString('vi-VN');
        setLogs(prev => {
            const next = [...prev, `[${ts}] ${msg}`];
            return next.length > 300 ? next.slice(-200) : next;
        });
    }, []);

    useEffect(() => {
        socket.emit('join-room', 'admin');

        const handlePosition = (data) => {
            setPosition({ x: data.x, y: data.y, direction: data.direction });
            setStepCount(data.stepCount || 0);
            setIsMoving(false);

            const dirName = DIR_NAMES[data.direction % 4];
            let logMsg = `📍 (${data.x}, ${data.y}) — Hướng: ${dirName}`;
            if (data.turned) {
                logMsg = `🔄 Rẽ ${data.turned === 'left' ? 'trái' : 'phải'} → Hướng: ${dirName} | Vị trí: (${data.x}, ${data.y})`;
            }
            if (data.steering !== undefined && data.steering !== null) {
                logMsg += ` | Steer: ${data.steering.toFixed(3)}`;
            }
            if (data.cannySteering !== undefined) {
                logMsg += ` | Canny: ${data.cannySteering.toFixed(3)}`;
            }
            if (data.unetSteering !== undefined) {
                logMsg += ` | UNet: ${data.unetSteering.toFixed(3)}`;
            }
            if (data.laneQuality !== undefined) {
                logMsg += ` | Lane: ${(data.laneQuality * 100).toFixed(0)}%`;
            }
            if (data.virtualLeft) {
                logMsg += ' | 🔸 VIRTUAL-L';
            }
            if (data.virtualRight) {
                logMsg += ' | 🔸 VIRTUAL-R';
            }
            if (data.driftBias !== undefined && Math.abs(data.driftBias) > 0.01) {
                logMsg += ` | Drift: ${data.driftBias.toFixed(3)}`;
            }
            if (data.centersCount !== undefined) {
                logMsg += ` | Centers: ${data.centersCount}`;
            }

            addLog(logMsg);

            // Set as pending point for approval
            if (!data.turned) {
                setPendingPoint({
                    x: data.x, y: data.y,
                    steering: data.steering,
                    laneQuality: data.laneQuality,
                    cannySteering: data.cannySteering,
                    unetSteering: data.unetSteering,
                    virtualLeft: data.virtualLeft,
                    virtualRight: data.virtualRight,
                    driftBias: data.driftBias,
                });
            }
        };

        const handleAnalysis = (data) => {
            setAnalysis(data.analysis);
            if (data.analysis) {
                let msg = `🔍 Canny: steer=${data.analysis.steering?.toFixed(3)} | quality=${(data.analysis.laneQuality * 100).toFixed(0)}% | centers=${data.analysis.centersCount}`;
                if (data.analysis.virtualLeft) msg += ' | VIRTUAL-L';
                if (data.analysis.virtualRight) msg += ' | VIRTUAL-R';
                addLog(msg);
            }
        };

        socket.on('map-build-position', handlePosition);
        socket.on('map-build-analysis', handleAnalysis);

        return () => {
            socket.off('map-build-position', handlePosition);
            socket.off('map-build-analysis', handleAnalysis);
        };
    }, [addLog]);

    // Road sign detection events
    useEffect(() => {
        const onStatus = (data) => {
            setSignDetecting(data.detecting);
            if (!data.detecting) setSignDetections([]);
        };
        const onDetected = (data) => {
            setSignDetections(data.detections || []);
            if (data.detections && data.detections.length > 0) {
                const names = data.detections.map(d => `${d.class} (${(d.confidence * 100).toFixed(0)}%)`);
                addLog(`🚦 Biển báo: ${names.join(', ')}`);
            }
        };
        const onResult = (data) => {
            setSignDetections(data.detections || []);
            if (data.detections && data.detections.length > 0) {
                const names = data.detections.map(d => `${d.class} (${(d.confidence * 100).toFixed(0)}%)`);
                addLog(`🚦 Phát hiện: ${names.join(', ')}`);
            } else {
                addLog('🚦 Không phát hiện biển báo');
            }
        };

        socket.on('sign-detect-status', onStatus);
        socket.on('sign-detected', onDetected);
        socket.on('sign-detect-result', onResult);
        return () => {
            socket.off('sign-detect-status', onStatus);
            socket.off('sign-detected', onDetected);
            socket.off('sign-detect-result', onResult);
        };
    }, [addLog]);

    // Auto-scroll logs
    useEffect(() => {
        if (logEndRef.current) logEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }, [logs]);

    // Move one step forward
    const handleStep = useCallback(() => {
        if (isMoving) return;
        setIsMoving(true);
        addLog(`⏩ Đang di chuyển 1 bước (speed=${stepSpeed}, duration=${stepDuration}s)...`);
        socket.emit('map-build-step', { duration: stepDuration, speed: stepSpeed });
    }, [isMoving, stepDuration, stepSpeed, addLog]);

    // Turn left/right
    const handleTurn = useCallback((direction) => {
        if (isMoving) return;
        setIsMoving(true);
        addLog(`🔄 Đang rẽ ${direction === 'left' ? 'trái' : 'phải'} (gấp đôi servo)...`);
        socket.emit('map-build-turn', { direction });
    }, [isMoving, addLog]);

    // Stop
    const handleStop = useCallback(() => {
        socket.emit('map-build-stop');
        setIsMoving(false);
        addLog('⏹ Dừng');
    }, [addLog]);

    // Request canny analysis
    const handleAnalyse = useCallback(() => {
        socket.emit('map-build-analyse');
        addLog('🔍 Đang phân tích canny + UNet...');
    }, [addLog]);

    // Toggle sign detection
    const toggleSignDetection = useCallback(() => {
        if (signDetecting) {
            socket.emit('sign-detect-stop');
            setSignDetecting(false);
            setSignDetections([]);
            addLog('🚦 Tắt nhận diện biển báo');
        } else {
            socket.emit('sign-detect-start');
            setSignDetecting(true);
            addLog('🚦 Bật nhận diện biển báo');
        }
    }, [signDetecting, addLog]);

    // Single-frame sign detect
    const handleSignDetectOnce = useCallback(() => {
        socket.emit('sign-detect-once');
        addLog('🚦 Đang phát hiện biển báo (1 frame)...');
    }, [addLog]);

    // Approve pending point
    const handleApprove = useCallback(() => {
        if (!pendingPoint) return;
        const label = labelInput.trim() || `P${approvedPoints.length}`;
        const point = { ...pendingPoint, label, approved: true };
        setApprovedPoints(prev => [...prev, point]);
        addLog(`✅ Duyệt toạ độ (${point.x}, ${point.y}) — Label: "${label}"`);
        setPendingPoint(null);
        setLabelInput('');
    }, [pendingPoint, labelInput, approvedPoints.length, addLog]);

    // Reject pending point
    const handleReject = useCallback(() => {
        if (!pendingPoint) return;
        addLog(`❌ Bỏ qua toạ độ (${pendingPoint.x}, ${pendingPoint.y})`);
        setPendingPoint(null);
    }, [pendingPoint, addLog]);

    // Remove last approved point
    const handleUndoLast = useCallback(() => {
        if (approvedPoints.length <= 1) return;
        const removed = approvedPoints[approvedPoints.length - 1];
        setApprovedPoints(prev => prev.slice(0, -1));
        addLog(`↩️ Đã xoá điểm "${removed.label}" (${removed.x}, ${removed.y})`);
    }, [approvedPoints, addLog]);

    // Screenshot
    const handleScreenshot = useCallback(async () => {
        const timestamp = new Date().toISOString().replace(/[:.]/g, '-');
        const filename = `map-capture-${timestamp}.jpg`;
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
                setScreenshotCount(prev => prev + 1);
                addLog(`📸 Đã chụp: ${filename}`);
                return;
            }
        } catch (e) { /* canvas tainted */ }
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
            setScreenshotCount(prev => prev + 1);
            addLog(`📸 Đã chụp: ${filename}`);
        } catch (e) {
            addLog('❌ Chụp ảnh thất bại');
        }
    }, [addLog]);

    // Export approved points as JSON
    const handleExport = useCallback(() => {
        const data = {
            exportedAt: new Date().toISOString(),
            totalPoints: approvedPoints.length,
            points: approvedPoints.map(p => ({ x: p.x, y: p.y, label: p.label })),
        };
        const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `map-points-${new Date().toISOString().replace(/[:.]/g, '-')}.json`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
        addLog(`💾 Đã xuất ${approvedPoints.length} điểm map`);
    }, [approvedPoints, addLog]);

    // Reset map
    const handleReset = useCallback(() => {
        setPosition({ x: 0, y: 0, direction: 0 });
        setStepCount(0);
        setApprovedPoints([{ x: 0, y: 0, label: 'S', approved: true }]);
        setPendingPoint(null);
        setAnalysis(null);
        setLogs([`[RESET] Xe ở vị trí (0, 0) — Hướng: ${DIR_NAMES[0]}`]);
        addLog('🔄 Đã reset map');
    }, [addLog]);

    // Keyboard shortcuts
    useEffect(() => {
        const handleKey = (e) => {
            if (e.target.tagName === 'INPUT') return;
            switch (e.key) {
                case ' ':
                    e.preventDefault();
                    if (pendingPoint) handleApprove();
                    else handleStep();
                    break;
                case 'Enter':
                    e.preventDefault();
                    if (pendingPoint) handleApprove();
                    else if (cameraOn) handleScreenshot();
                    break;
                case 'Escape':
                    if (pendingPoint) handleReject();
                    break;
                case 'a': case 'ArrowLeft':
                    if (!pendingPoint) handleTurn('left');
                    break;
                case 'd': case 'ArrowRight':
                    if (!pendingPoint) handleTurn('right');
                    break;
                case 'w': case 'ArrowUp':
                    if (!pendingPoint) handleStep();
                    break;
                default: break;
            }
        };
        window.addEventListener('keydown', handleKey);
        return () => window.removeEventListener('keydown', handleKey);
    }, [pendingPoint, handleApprove, handleReject, handleStep, handleTurn, handleScreenshot, cameraOn]);

    // Grid bounds
    const allPoints = [...approvedPoints];
    if (pendingPoint) allPoints.push(pendingPoint);
    allPoints.push(position);
    const minX = Math.min(...allPoints.map(p => p.x)) - 2;
    const maxX = Math.max(...allPoints.map(p => p.x)) + 2;
    const minY = Math.min(...allPoints.map(p => p.y)) - 2;
    const maxY = Math.max(...allPoints.map(p => p.y)) + 2;
    const gridW = maxX - minX + 1;
    const gridH = maxY - minY + 1;
    const cellSize = Math.min(28, Math.max(16, Math.floor(400 / Math.max(gridW, gridH))));

    const currentStreamUrl = `${PROCESSED_URL}?mode=${cameraMode}`;

    return (
        <div className="max-w-7xl mx-auto">
            <div className="flex items-center justify-between mb-5">
                <h1 className="text-2xl font-bold text-gray-900">🗺️ Map Builder — Dò bản đồ</h1>
                <div className="flex items-center gap-2">
                    <button onClick={handleReset} className="px-3 py-1.5 text-xs bg-red-50 text-red-600 rounded-lg hover:bg-red-100 border border-red-200 transition">
                        <TrashIcon className="w-3.5 h-3.5 inline mr-1" /> Reset
                    </button>
                    <button onClick={handleExport} disabled={approvedPoints.length <= 1}
                        className="px-3 py-1.5 text-xs bg-blue-50 text-blue-600 rounded-lg hover:bg-blue-100 border border-blue-200 transition disabled:opacity-40">
                        <ArrowDownTrayIcon className="w-3.5 h-3.5 inline mr-1" /> Export JSON
                    </button>
                </div>
            </div>

            <div className="grid grid-cols-1 xl:grid-cols-12 gap-5">

                {/* -- Camera + Controls (col 1-5) -- */}
                <div className="xl:col-span-5 space-y-4">
                    {/* Camera */}
                    <div className="bg-white rounded-2xl border border-gray-200 overflow-hidden">
                        <div className="flex items-center justify-between px-4 py-2.5 border-b bg-gray-50">
                            <h2 className="text-sm font-semibold text-gray-600 flex items-center gap-1.5">
                                <VideoCameraIcon className="w-4 h-4" /> Camera
                            </h2>
                            <div className="flex items-center gap-1.5">
                                {['raw', 'canny', 'unet', 'sign', 'all'].map(mode => (
                                    <button key={mode} onClick={() => setCameraMode(mode)}
                                        className={`px-2 py-1 text-[10px] font-medium rounded transition ${cameraMode === mode
                                                ? mode === 'all' ? 'bg-amber-500 text-white' : 'bg-purple-500 text-white'
                                                : 'bg-gray-100 text-gray-500 hover:bg-gray-200'
                                            }`}>
                                        {{ raw: 'Raw', canny: 'Canny', unet: 'UNet', sign: 'Sign', all: 'All' }[mode]}
                                    </button>
                                ))}
                                {cameraOn && (
                                    <button onClick={handleScreenshot}
                                        className="px-2 py-1 text-[10px] font-medium rounded bg-blue-500 text-white hover:bg-blue-600 transition inline-flex items-center gap-0.5">
                                        <CameraIcon className="w-3 h-3" /> ({screenshotCount})
                                    </button>
                                )}
                                <button onClick={() => setCameraOn(!cameraOn)}
                                    className={`px-2.5 py-1 text-[10px] font-medium rounded transition ${cameraOn ? 'bg-red-500 text-white' : 'bg-amber-500 text-white'}`}>
                                    {cameraOn ? 'Ngắt' : 'Kết nối'}
                                </button>
                            </div>
                        </div>
                        <div className="aspect-video bg-gray-900 flex items-center justify-center">
                            {cameraOn ? (
                                <img ref={cameraImgRef} src={currentStreamUrl} alt="Camera"
                                    className="w-full h-full object-contain" crossOrigin="anonymous"
                                    onError={() => { if (cameraMode !== 'raw') setCameraMode('raw'); else setCameraOn(false); }} />
                            ) : (
                                <div className="text-gray-500 text-sm text-center">
                                    <VideoCameraIcon className="w-8 h-8 mx-auto mb-2 opacity-40" />
                                    Nhấn "Kết nối"
                                </div>
                            )}
                        </div>
                    </div>

                    {/* Controls */}
                    <div className="bg-white rounded-2xl border border-gray-200 p-4">
                        <h2 className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-3">Điều khiển dò map</h2>

                        {/* Current position */}
                        <div className="flex items-center gap-4 mb-4 p-3 bg-gray-50 rounded-xl">
                            <div className="text-center">
                                <span className="text-2xl">{DIR_ARROWS[position.direction % 4]}</span>
                                <p className="text-[10px] text-gray-400 mt-0.5">{DIR_NAMES[position.direction % 4]}</p>
                            </div>
                            <div>
                                <p className="text-sm font-bold text-gray-800">
                                    Toạ độ: <span className="text-amber-600">({position.x}, {position.y})</span>
                                </p>
                                <p className="text-xs text-gray-400">Bước: {stepCount} | Đã duyệt: {approvedPoints.length} điểm</p>
                            </div>
                        </div>

                        {/* Move buttons */}
                        <div className="flex items-center justify-center gap-3 mb-4">
                            <button onClick={() => handleTurn('left')} disabled={isMoving || !!pendingPoint}
                                className="w-14 h-14 rounded-xl bg-white border border-gray-200 flex items-center justify-center hover:bg-amber-50 hover:border-amber-300 disabled:opacity-40 transition shadow-sm">
                                <ArrowUturnLeftIcon className="w-6 h-6 text-gray-600" />
                            </button>
                            <button onClick={handleStep} disabled={isMoving || !!pendingPoint}
                                className="w-14 h-14 rounded-xl bg-amber-500 text-white flex items-center justify-center hover:bg-amber-600 disabled:opacity-40 transition shadow-md active:scale-95">
                                <ArrowUpIcon className="w-6 h-6" />
                            </button>
                            <button onClick={() => handleTurn('right')} disabled={isMoving || !!pendingPoint}
                                className="w-14 h-14 rounded-xl bg-white border border-gray-200 flex items-center justify-center hover:bg-amber-50 hover:border-amber-300 disabled:opacity-40 transition shadow-sm">
                                <ArrowUturnRightIcon className="w-6 h-6 text-gray-600" />
                            </button>
                            <button onClick={handleStop}
                                className="w-14 h-14 rounded-xl bg-red-500 text-white flex items-center justify-center hover:bg-red-600 transition shadow-md active:scale-95">
                                <StopIcon className="w-6 h-6" />
                            </button>
                        </div>

                        {/* Step config */}
                        <div className="grid grid-cols-2 gap-3 mb-4">
                            <div>
                                <label className="text-[10px] text-gray-400">Duration (s)</label>
                                <input type="number" min="0.2" max="3" step="0.1" value={stepDuration}
                                    onChange={e => setStepDuration(parseFloat(e.target.value) || 0.6)}
                                    className="w-full px-2 py-1.5 text-sm border rounded-lg focus:ring-1 focus:ring-amber-400 focus:border-amber-400" />
                            </div>
                            <div>
                                <label className="text-[10px] text-gray-400">Speed</label>
                                <input type="number" min="10" max="100" step="5" value={stepSpeed}
                                    onChange={e => setStepSpeed(parseInt(e.target.value) || 40)}
                                    className="w-full px-2 py-1.5 text-sm border rounded-lg focus:ring-1 focus:ring-amber-400 focus:border-amber-400" />
                            </div>
                        </div>

                        {/* Analyse button */}
                        <button onClick={handleAnalyse}
                            className="w-full px-3 py-2 text-sm bg-purple-50 text-purple-600 rounded-lg border border-purple-200 hover:bg-purple-100 transition mb-3">
                            🔍 Phân tích Canny + UNet
                        </button>

                        {/* Road sign detection */}
                        <div className="flex gap-2 mb-3">
                            <button onClick={toggleSignDetection}
                                className={`flex-1 px-3 py-2 text-sm rounded-lg border transition flex items-center justify-center gap-1.5 ${signDetecting
                                    ? 'bg-red-50 text-red-600 border-red-200 hover:bg-red-100'
                                    : 'bg-green-50 text-green-600 border-green-200 hover:bg-green-100'
                                    }`}>
                                <EyeIcon className="w-4 h-4" />
                                {signDetecting ? 'Tắt biển báo' : '🚦 Bật biển báo'}
                            </button>
                            <button onClick={handleSignDetectOnce} disabled={signDetecting}
                                className="px-3 py-2 text-sm bg-amber-50 text-amber-600 rounded-lg border border-amber-200 hover:bg-amber-100 transition disabled:opacity-40">
                                1 Frame
                            </button>
                        </div>

                        {/* Sign detection results */}
                        {signDetections.length > 0 && (
                            <div className="p-3 rounded-xl bg-green-50 border border-green-200 mb-3">
                                <h3 className="text-[10px] font-semibold text-green-600 uppercase tracking-wide mb-2 flex items-center gap-1">
                                    <EyeIcon className="w-3.5 h-3.5" /> Biển báo phát hiện ({signDetections.length})
                                </h3>
                                <div className="space-y-1.5">
                                    {signDetections.map((d, i) => (
                                        <div key={i} className="flex items-center justify-between text-xs">
                                            <span className="font-medium text-gray-700">🚦 {d.class}</span>
                                            <span className="font-mono text-green-700 font-bold">
                                                {(d.confidence * 100).toFixed(1)}%
                                            </span>
                                        </div>
                                    ))}
                                </div>
                            </div>
                        )}
                        {signDetecting && signDetections.length === 0 && (
                            <div className="p-2 rounded-xl bg-gray-50 border border-gray-200 mb-3 text-center">
                                <span className="text-xs text-gray-400 flex items-center justify-center gap-1">
                                    <span className="w-2 h-2 bg-green-500 rounded-full animate-pulse" />
                                    Đang quét biển báo...
                                </span>
                            </div>
                        )}

                        {/* Analysis result */}
                        {analysis && (
                            <div className="p-3 rounded-xl bg-purple-50 border border-purple-100 text-xs space-y-1 mb-3">
                                <div className="flex justify-between"><span className="text-gray-500">Steering:</span><span className="font-mono font-bold">{analysis.steering?.toFixed(3) ?? 'N/A'}</span></div>
                                <div className="flex justify-between"><span className="text-gray-500">Lane Quality:</span><span className="font-mono">{((analysis.laneQuality || 0) * 100).toFixed(0)}%</span></div>
                                <div className="flex justify-between"><span className="text-gray-500">Centers:</span><span className="font-mono">{analysis.centersCount}</span></div>
                                <div className="flex justify-between"><span className="text-gray-500">Midpoint:</span><span className="font-mono">{analysis.midpoint ? `(${analysis.midpoint.x.toFixed(0)}, ${analysis.midpoint.y.toFixed(0)})` : 'N/A'}</span></div>
                                {analysis.virtualLeft && (
                                    <div className="flex justify-between"><span className="text-gray-500">🔸 Virtual:</span><span className="font-mono text-orange-600">LEFT inferred</span></div>
                                )}
                                {analysis.virtualRight && (
                                    <div className="flex justify-between"><span className="text-gray-500">🔸 Virtual:</span><span className="font-mono text-orange-600">RIGHT inferred</span></div>
                                )}
                            </div>
                        )}

                        <p className="text-[10px] text-gray-300 text-center">
                            W/↑ = Bước tiến · A/← = Rẽ trái · D/→ = Rẽ phải · Space = Duyệt/Bước · Enter = Duyệt/Chụp · Esc = Bỏ qua
                        </p>
                    </div>

                </div>

                {/* -- Pending Approval + Grid + Log (col 6-12) -- */}
                <div className="xl:col-span-7 space-y-4">

                    {/* Pending point approval */}
                    {pendingPoint && (
                        <div className="bg-yellow-50 border-2 border-yellow-300 rounded-2xl p-5 shadow-lg animate-pulse-slow">
                            <h3 className="text-sm font-bold text-yellow-700 mb-3 flex items-center gap-2">
                                <MapPinIcon className="w-5 h-5" /> Duyệt toạ độ mới
                            </h3>
                            <div className="flex items-center gap-4 mb-4">
                                <div className="text-center p-3 bg-white rounded-xl border border-yellow-200">
                                    <p className="text-2xl font-bold text-amber-600">({pendingPoint.x}, {pendingPoint.y})</p>
                                    <p className="text-xs text-gray-400 mt-1">
                                        Steer: {pendingPoint.steering?.toFixed(3) ?? 'N/A'} | Quality: {((pendingPoint.laneQuality || 0) * 100).toFixed(0)}%
                                    </p>
                                    {pendingPoint.cannySteering !== undefined && (
                                        <p className="text-[10px] text-gray-300 mt-0.5">
                                            Canny: {pendingPoint.cannySteering?.toFixed(3)} | UNet: {pendingPoint.unetSteering?.toFixed(3)}
                                        </p>
                                    )}
                                    {(pendingPoint.virtualLeft || pendingPoint.virtualRight) && (
                                        <p className="text-[10px] text-orange-500 mt-0.5">🔸 Virtual: {pendingPoint.virtualLeft ? 'L' : ''}{pendingPoint.virtualRight ? 'R' : ''}</p>
                                    )}
                                    {pendingPoint.driftBias !== undefined && Math.abs(pendingPoint.driftBias) > 0.01 && (
                                        <p className="text-[10px] text-cyan-500 mt-0.5">⚖️ Drift bias: {pendingPoint.driftBias.toFixed(3)}</p>
                                    )}
                                </div>
                                <div className="flex-1">
                                    <label className="text-xs text-gray-500 mb-1 block">Label (tuỳ chọn)</label>
                                    <input type="text" value={labelInput} onChange={e => setLabelInput(e.target.value)}
                                        placeholder={`P${approvedPoints.length}`}
                                        className="w-full px-3 py-2 text-sm border rounded-lg focus:ring-2 focus:ring-yellow-400 focus:border-yellow-400"
                                        autoFocus />
                                </div>
                            </div>
                            <div className="flex gap-3">
                                <button onClick={handleApprove}
                                    className="flex-1 inline-flex items-center justify-center gap-2 px-4 py-2.5 bg-green-500 text-white rounded-xl text-sm font-bold hover:bg-green-600 transition shadow-lg">
                                    <CheckCircleIcon className="w-5 h-5" /> Duyệt (Space/Enter)
                                </button>
                                <button onClick={handleReject}
                                    className="flex-1 inline-flex items-center justify-center gap-2 px-4 py-2.5 bg-red-500 text-white rounded-xl text-sm font-bold hover:bg-red-600 transition shadow-lg">
                                    <XCircleIcon className="w-5 h-5" /> Bỏ qua (Esc)
                                </button>
                            </div>
                        </div>
                    )}

                    {/* Grid map visualization */}
                    <div className="bg-white rounded-2xl border border-gray-200 overflow-hidden">
                        <div className="flex items-center justify-between px-4 py-2.5 border-b bg-gray-50">
                            <h2 className="text-sm font-semibold text-gray-600 flex items-center gap-1.5">
                                <MapPinIcon className="w-4 h-4" /> Bản đồ toạ độ
                            </h2>
                            <div className="flex items-center gap-2">
                                <span className="text-[10px] text-gray-400">{approvedPoints.length} điểm đã duyệt</span>
                                <button onClick={handleUndoLast} disabled={approvedPoints.length <= 1}
                                    className="text-[10px] text-red-400 hover:text-red-600 disabled:opacity-30 transition">
                                    ↩ Undo
                                </button>
                            </div>
                        </div>
                        <div className="p-4 overflow-auto" style={{ maxHeight: 360 }}>
                            <div className="inline-block border border-gray-100 rounded-lg overflow-hidden" style={{ lineHeight: 0 }}>
                                {Array.from({ length: gridH }, (_, rowIdx) => {
                                    const gy = maxY - rowIdx;
                                    return (
                                        <div key={gy} className="flex" style={{ height: cellSize }}>
                                            {Array.from({ length: gridW }, (_, colIdx) => {
                                                const gx = minX + colIdx;
                                                const isVehicle = gx === position.x && gy === position.y;
                                                const approved = approvedPoints.find(p => p.x === gx && p.y === gy);
                                                const isPending = pendingPoint && pendingPoint.x === gx && pendingPoint.y === gy;
                                                const isStart = gx === 0 && gy === 0;

                                                let bg = 'bg-gray-50';
                                                let content = '';
                                                let textColor = 'text-gray-300';

                                                if (isVehicle) {
                                                    bg = 'bg-amber-400';
                                                    content = DIR_ARROWS[position.direction % 4];
                                                    textColor = 'text-white font-bold';
                                                } else if (isPending) {
                                                    bg = 'bg-yellow-200 animate-pulse';
                                                    content = '?';
                                                    textColor = 'text-yellow-700 font-bold';
                                                } else if (approved) {
                                                    bg = isStart ? 'bg-green-400' : 'bg-blue-400';
                                                    content = approved.label?.charAt(0) || '·';
                                                    textColor = 'text-white font-bold';
                                                }

                                                return (
                                                    <div key={gx} title={`(${gx}, ${gy})${approved ? ` — ${approved.label}` : ''}`}
                                                        className={`inline-flex items-center justify-center border border-gray-100 ${bg} ${textColor}`}
                                                        style={{ width: cellSize, height: cellSize, fontSize: cellSize * 0.45, lineHeight: 1 }}>
                                                        {content}
                                                    </div>
                                                );
                                            })}
                                        </div>
                                    );
                                })}
                            </div>
                            <div className="flex items-center gap-4 mt-3 text-[10px] text-gray-400">
                                <span><span className="inline-block w-3 h-3 bg-green-400 rounded mr-1" /> Start</span>
                                <span><span className="inline-block w-3 h-3 bg-blue-400 rounded mr-1" /> Đã duyệt</span>
                                <span><span className="inline-block w-3 h-3 bg-amber-400 rounded mr-1" /> Xe</span>
                                <span><span className="inline-block w-3 h-3 bg-yellow-200 rounded mr-1" /> Chờ duyệt</span>
                            </div>
                        </div>
                    </div>

                    {/* Approved points list */}
                    <div className="bg-white rounded-2xl border border-gray-200 overflow-hidden">
                        <div className="flex items-center justify-between px-4 py-2.5 border-b bg-gray-50">
                            <h2 className="text-sm font-semibold text-gray-600">📋 Toạ độ đã duyệt</h2>
                        </div>
                        <div className="max-h-40 overflow-y-auto p-3">
                            <div className="flex flex-wrap gap-1.5">
                                {approvedPoints.map((p, i) => (
                                    <span key={i}
                                        className={`inline-flex items-center gap-1 px-2 py-1 rounded-lg text-[11px] font-mono ${i === 0 ? 'bg-green-100 text-green-700' : 'bg-blue-50 text-blue-700'}`}>
                                        <span className="font-bold">{p.label}</span>
                                        ({p.x},{p.y})
                                    </span>
                                ))}
                            </div>
                        </div>
                    </div>

                    {/* Log panel */}
                    <div className="bg-white rounded-2xl border border-gray-200 overflow-hidden flex flex-col" style={{ maxHeight: 320 }}>
                        <div className="flex items-center justify-between px-4 py-2.5 border-b bg-gray-50">
                            <h2 className="text-sm font-semibold text-gray-600">📝 Log dò map (Canny + UNet)</h2>
                            <button onClick={() => setLogs([])}
                                className="text-[10px] text-gray-400 hover:text-red-500 transition">Xoá</button>
                        </div>
                        <div className="flex-1 overflow-y-auto p-3 font-mono text-[11px] leading-relaxed bg-gray-900 text-green-400">
                            {logs.map((log, idx) => (
                                <div key={idx} className={`py-0.5 ${log.includes('✅') ? 'text-green-300 font-bold' :
                                    log.includes('❌') ? 'text-red-400' :
                                        log.includes('VIRTUAL') ? 'text-orange-400' :
                                            log.includes('Drift') ? 'text-cyan-300' :
                                                log.includes('🔄') ? 'text-yellow-300' :
                                                    log.includes('Canny') ? 'text-purple-300' :
                                                        log.includes('UNet') ? 'text-cyan-300' :
                                                            log.includes('📸') ? 'text-blue-300' :
                                                                log.includes('🚦') ? 'text-emerald-300' : ''
                                    }`}>
                                    {log}
                                </div>
                            ))}
                            <div ref={logEndRef} />
                        </div>
                    </div>
                </div>
            </div>
        </div >
    );
}
