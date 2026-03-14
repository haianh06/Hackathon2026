import React, { useState, useEffect, useRef } from 'react';
import socket from '../services/socket';
import { VideoCameraIcon, MapPinIcon, EyeIcon } from '@heroicons/react/24/outline';

export default function StaffCameraPage() {
    const [cameraOn, setCameraOn] = useState(false);
    const [navLogs, setNavLogs] = useState([]);
    const logEndRef = useRef(null);

    // Road sign detection state
    const [signDetecting, setSignDetecting] = useState(false);
    const [detections, setDetections] = useState([]);
    const detectionsRef = useRef(null);

    const STREAM_URL = '/camera/stream';

    useEffect(() => {
        const handler = (data) => {
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

        socket.on('navigation-log', handler);
        return () => socket.off('navigation-log', handler);
    }, []);

    // Auto-scroll
    useEffect(() => {
        if (logEndRef.current) logEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }, [navLogs]);

    // Road sign detection events
    useEffect(() => {
        const onStatus = (data) => {
            setSignDetecting(data.detecting);
        };
        const onDetected = (data) => {
            setDetections(data.detections || []);
        };
        const onResult = (data) => {
            setDetections(data.detections || []);
        };

        socket.on('sign-detect-status', onStatus);
        socket.on('sign-detected', onDetected);
        socket.on('sign-detect-result', onResult);
        return () => {
            socket.off('sign-detect-status', onStatus);
            socket.off('sign-detected', onDetected);
            socket.off('sign-detect-result', onResult);
        };
    }, []);

    const toggleSignDetection = () => {
        if (signDetecting) {
            socket.emit('sign-detect-stop');
            setSignDetecting(false);
            setDetections([]);
        } else {
            socket.emit('sign-detect-start');
            setSignDetecting(true);
        }
    };

    return (
        <div className="max-w-5xl mx-auto">
            <h1 className="text-2xl font-bold text-gray-900 mb-6">Camera & Giám sát</h1>

            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                {/* Camera panel */}
                <div className="bg-white rounded-2xl border border-gray-200 overflow-hidden">
                    <div className="flex items-center justify-between px-5 py-3 border-b bg-gray-50">
                        <h2 className="text-sm font-semibold text-gray-600 flex items-center gap-1.5">
                            <VideoCameraIcon className="w-4 h-4" /> Camera MJPEG
                        </h2>
                        <div className="flex items-center gap-2">
                            <span className={`w-2 h-2 rounded-full ${cameraOn ? 'bg-green-500 animate-pulse' : 'bg-gray-300'}`} />
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
                            <img src={STREAM_URL} alt="Camera" className="w-full h-full object-contain"
                                onError={() => setCameraOn(false)} />
                        ) : (
                            <div className="text-gray-500 text-sm text-center">
                                <VideoCameraIcon className="w-10 h-10 mx-auto mb-2 opacity-40" />
                                Nhấn "Kết nối" để xem camera
                            </div>
                        )}
                        {/* Detection overlay badge */}
                        {signDetecting && detections.length > 0 && (
                            <div className="absolute top-2 left-2 flex flex-col gap-1">
                                {detections.map((d, i) => (
                                    <span key={i} className="bg-red-500 text-white text-xs font-bold px-2 py-1 rounded-lg shadow-lg animate-pulse">
                                        🚦 {d.class} ({(d.confidence * 100).toFixed(0)}%)
                                    </span>
                                ))}
                            </div>
                        )}
                    </div>
                </div>

                {/* Navigation log panel */}
                <div className="bg-white rounded-2xl border border-gray-200 overflow-hidden flex flex-col" style={{ maxHeight: 500 }}>
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
                                    log.startsWith('✅') ? 'text-green-300' :
                                        ''
                                }`}>
                                {log}
                            </div>
                        ))}
                        <div ref={logEndRef} />
                    </div>
                </div>
            </div>

            {/* Road Sign Detection Panel */}
            <div className="mt-6 bg-white rounded-2xl border border-gray-200 overflow-hidden">
                <div className="flex items-center justify-between px-5 py-3 border-b bg-gray-50">
                    <h2 className="text-sm font-semibold text-gray-600 flex items-center gap-1.5">
                        <EyeIcon className="w-4 h-4" /> Nhận diện biển báo giao thông
                    </h2>
                    <div className="flex items-center gap-3">
                        {signDetecting && (
                            <span className="flex items-center gap-1 text-xs text-green-600">
                                <span className="w-2 h-2 bg-green-500 rounded-full animate-pulse" />
                                Đang quét
                            </span>
                        )}
                        <button onClick={toggleSignDetection}
                            className={`px-3 py-1.5 text-xs font-medium rounded-lg transition ${signDetecting
                                ? 'bg-red-500 text-white hover:bg-red-600'
                                : 'bg-indigo-500 text-white hover:bg-indigo-600'
                                }`}>
                            {signDetecting ? 'Dừng nhận diện' : '🚦 Bật nhận diện'}
                        </button>
                        <button onClick={() => socket.emit('sign-detect-once')}
                            disabled={signDetecting}
                            className="px-3 py-1.5 text-xs font-medium rounded-lg bg-gray-100 text-gray-600 hover:bg-gray-200 transition disabled:opacity-40">
                            Chụp 1 lần
                        </button>
                    </div>
                </div>

                <div className="p-5">
                    {detections.length === 0 ? (
                        <div className="text-center py-8 text-gray-400 text-sm">
                            {signDetecting
                                ? 'Đang quét... chưa phát hiện biển báo nào'
                                : 'Nhấn "Bật nhận diện" để bắt đầu nhận diện biển báo từ camera'
                            }
                        </div>
                    ) : (
                        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-3" ref={detectionsRef}>
                            {detections.map((d, i) => (
                                <div key={i} className="bg-gray-50 rounded-xl border border-gray-200 p-3 text-center">
                                    <div className="text-3xl mb-1">🚦</div>
                                    <p className="text-sm font-bold text-gray-800">{d.class}</p>
                                    <p className="text-xs text-gray-400">
                                        Độ tin cậy: <span className={`font-bold ${d.confidence > 0.8 ? 'text-green-600' : d.confidence > 0.6 ? 'text-amber-600' : 'text-red-500'}`}>
                                            {(d.confidence * 100).toFixed(1)}%
                                        </span>
                                    </p>
                                    <p className="text-[10px] text-gray-300 mt-1">
                                        [{d.bbox?.join(', ')}]
                                    </p>
                                </div>
                            ))}
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}
