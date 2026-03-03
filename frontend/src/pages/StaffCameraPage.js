import React, { useState, useEffect, useRef } from 'react';
import socket from '../services/socket';
import { VideoCameraIcon, MapPinIcon } from '@heroicons/react/24/outline';

export default function StaffCameraPage() {
    const [cameraOn, setCameraOn] = useState(false);
    const [navLogs, setNavLogs] = useState([]);
    const logEndRef = useRef(null);

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
                    <div className="aspect-video bg-gray-900 flex items-center justify-center">
                        {cameraOn ? (
                            <img src={STREAM_URL} alt="Camera" className="w-full h-full object-contain"
                                onError={() => setCameraOn(false)} />
                        ) : (
                            <div className="text-gray-500 text-sm text-center">
                                <VideoCameraIcon className="w-10 h-10 mx-auto mb-2 opacity-40" />
                                Nhấn "Kết nối" để xem camera
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
        </div>
    );
}
