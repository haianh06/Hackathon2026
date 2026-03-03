import React, { useState, useEffect, useRef, useCallback } from 'react';
import { getMapPoints, getVehicleStatus, findPath, seedMap } from '../services/api';
import socket from '../services/socket';
import DemoMap from '../components/DemoMap';
import { MapPinIcon, ArrowPathIcon, PlayIcon, StopIcon } from '@heroicons/react/24/outline';

function MapPage() {
    const [points, setPoints] = useState([]);
    const [vehicle, setVehicle] = useState(null);
    const [activePath, setActivePath] = useState(null);
    const [pathFrom, setPathFrom] = useState('S');
    const [pathTo, setPathTo] = useState('');
    const [cameraOn, setCameraOn] = useState(false);

    const [isNavigating, setIsNavigating] = useState(false);
    const [livePos, setLivePos] = useState(null);
    const [navLogs, setNavLogs] = useState([]);
    const logEndRef = useRef(null);

    useEffect(() => {
        loadMapData();
        socket.on('vehicle-position', (data) => setVehicle(prev => prev ? { ...prev, currentPosition: data.pointId } : prev));
        socket.on('vehicle-dispatch', (data) => { if (data.path) setActivePath(data.path); });
        socket.on('vehicle-returned', () => { setActivePath(null); setLivePos(null); setVehicle(prev => prev ? { ...prev, currentPosition: 'S', status: 'idle' } : prev); });
        socket.on('navigation-log', (data) => {
            const ts = data.timestamp ? new Date(data.timestamp * 1000).toLocaleTimeString('vi-VN') : '';
            if (data.type === 'start') { setIsNavigating(true); setLivePos({ x: data.x, y: data.y }); addLog(`🚀 [${ts}] Bắt đầu — Tuyến: ${(data.route || []).join(' → ')}`); }
            else if (data.type === 'moving') { setLivePos({ x: data.x, y: data.y }); addLog(`📍 [${ts}] ${data.fromPoint} → ${data.toPoint} (${data.x}, ${data.y}) ${data.progress}%`); }
            else if (data.type === 'waypoint') { setLivePos({ x: data.x, y: data.y }); addLog(`✅ [${ts}] Đã đến ${data.pointId} (${data.x}, ${data.y})`); }
            else if (data.type === 'complete') { setIsNavigating(false); addLog(`🏁 [${ts}] HOÀN THÀNH — ${data.pointId} | ${(data.duration || 0)}s`); }
            else if (data.type === 'cancelled') { setIsNavigating(false); addLog(`⏹ [${ts}] Đã dừng`); }
            else if (data.type === 'line-correct') { addLog(`🔧 [${ts}] Bám làn: ${data.correction < 0 ? '◀' : '▶'} ${data.correction > 0 ? '+' : ''}${data.correction} ${data.steerTime}s`); }
        });
        return () => { socket.off('vehicle-position'); socket.off('vehicle-dispatch'); socket.off('vehicle-returned'); socket.off('navigation-log'); };
    }, []); // eslint-disable-line react-hooks/exhaustive-deps

    const addLog = useCallback((msg) => { setNavLogs(prev => { const n = [...prev, msg]; return n.length > 200 ? n.slice(-150) : n; }); }, []);
    useEffect(() => { if (logEndRef.current) logEndRef.current.scrollIntoView({ behavior: 'smooth' }); }, [navLogs]);

    const loadMapData = async () => {
        try {
            const [mapRes, vehicleRes] = await Promise.all([getMapPoints(), getVehicleStatus()]);
            let pts = mapRes.data.data;
            if (!pts || pts.length === 0) { const r = await seedMap(); pts = r.data.data; }
            setPoints(pts);
            setVehicle(vehicleRes.data.data);
        } catch (err) { console.error(err); }
    };

    const handleResetMap = async () => { try { const r = await seedMap(); setPoints(r.data.data); setActivePath(null); setLivePos(null); } catch (e) { console.error(e); } };
    const handleFindPath = async () => { if (!pathFrom || !pathTo) return; try { const r = await findPath(pathFrom, pathTo); setActivePath(r.data.data); } catch (e) { console.error(e); } };
    const handleAutoNavigate = () => { if (!activePath || activePath.length < 2) return; setNavLogs([]); setLivePos(null); socket.emit('auto-navigate', { path: activePath.map(p => ({ pointId: p.pointId, x: p.x, y: p.y })) }); };
    const handleStopNavigation = () => { socket.emit('stop-navigation'); setIsNavigating(false); };

    const statusMap = { idle: 'Sẵn sàng', moving: 'Đang di chuyển', delivering: 'Đang giao', returning: 'Quay về' };
    const statusColor = { idle: 'text-green-600', moving: 'text-yellow-600', delivering: 'text-amber-600', returning: 'text-blue-600' };

    return (
        <div className="max-w-6xl mx-auto">
            <h1 className="text-2xl font-bold text-gray-900 mb-6 flex items-center gap-2">
                <MapPinIcon className="w-7 h-7 text-amber-500" /> Bản đồ & Xe tự hành
            </h1>

            <div className="grid grid-cols-1 lg:grid-cols-5 gap-6">
                {/* Left: Map (3 cols) */}
                <div className="lg:col-span-3 space-y-4">
                    <div className="bg-white rounded-2xl border border-gray-200 p-4">
                        <DemoMap points={points} vehiclePosition={vehicle?.currentPosition} activePath={activePath} livePos={livePos} />
                        {/* Legend */}
                        <div className="flex flex-wrap gap-4 mt-3 text-xs text-gray-500">
                            {[['#34A853', 'Start'], ['#EA4335', 'Stop'], ['#FB8C00', 'Ngã tư'], ['#607D8B', 'Trung gian']].map(([c, l]) => (
                                <span key={l} className="flex items-center gap-1"><span className="w-3 h-3 rounded-full inline-block" style={{ background: c }} />{l}</span>
                            ))}
                        </div>
                    </div>

                    {/* Camera */}
                    <div className="bg-white rounded-2xl border border-gray-200 overflow-hidden">
                        <div className="flex items-center justify-between px-4 py-2.5 border-b bg-gray-50">
                            <span className="text-sm font-medium text-gray-600">Camera MJPEG</span>
                            <button onClick={() => setCameraOn(!cameraOn)} className={`px-3 py-1 text-xs font-medium rounded-lg ${cameraOn ? 'bg-red-500 text-white' : 'bg-amber-500 text-white'}`}>
                                {cameraOn ? 'Ngắt' : 'Kết nối'}
                            </button>
                        </div>
                        <div className="aspect-video bg-gray-900 flex items-center justify-center">
                            {cameraOn ? <img src="/camera/stream" alt="Camera" className="w-full h-full object-contain" onError={() => setCameraOn(false)} />
                                : <span className="text-gray-500 text-sm">Nhấn "Kết nối"</span>}
                        </div>
                    </div>
                </div>

                {/* Right: Controls (2 cols) */}
                <div className="lg:col-span-2 space-y-4">
                    {/* Path finder */}
                    <div className="bg-white rounded-2xl border border-gray-200 p-5">
                        <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wide mb-3">Tìm đường (Dijkstra)</h2>
                        <div className="space-y-2">
                            <select value={pathFrom} onChange={e => setPathFrom(e.target.value)} className="w-full rounded-lg border-gray-200 text-sm focus:ring-amber-500 focus:border-amber-500">
                                {points.map(p => <option key={p.pointId} value={p.pointId}>{p.label} ({p.pointId})</option>)}
                            </select>
                            <select value={pathTo} onChange={e => setPathTo(e.target.value)} className="w-full rounded-lg border-gray-200 text-sm focus:ring-amber-500 focus:border-amber-500">
                                <option value="">-- Chọn đích --</option>
                                {points.map(p => <option key={p.pointId} value={p.pointId}>{p.label} ({p.pointId})</option>)}
                            </select>
                            <div className="flex gap-2">
                                <button onClick={handleFindPath} className="flex-1 px-3 py-2 bg-amber-500 text-white text-sm rounded-lg hover:bg-amber-600 transition">Tìm đường</button>
                                <button onClick={() => { setActivePath(null); setLivePos(null); }} className="px-3 py-2 bg-gray-100 text-gray-600 text-sm rounded-lg hover:bg-gray-200 transition">Xoá</button>
                                <button onClick={handleResetMap} className="px-3 py-2 bg-gray-100 text-gray-600 text-sm rounded-lg hover:bg-gray-200 transition"><ArrowPathIcon className="w-4 h-4" /></button>
                            </div>
                        </div>

                        {activePath && activePath.length > 1 && (
                            <div className="mt-3 p-3 bg-blue-50 rounded-xl border border-blue-100">
                                <p className="text-xs font-medium text-blue-700 mb-2">Tuyến: {activePath.map(p => p.pointId).join(' → ')}</p>
                                {!isNavigating ? (
                                    <button onClick={handleAutoNavigate} className="w-full inline-flex items-center justify-center gap-1.5 px-3 py-2 bg-green-500 text-white text-sm rounded-lg hover:bg-green-600 transition">
                                        <PlayIcon className="w-4 h-4" /> Tự động chạy xe
                                    </button>
                                ) : (
                                    <button onClick={handleStopNavigation} className="w-full inline-flex items-center justify-center gap-1.5 px-3 py-2 bg-red-500 text-white text-sm rounded-lg hover:bg-red-600 transition">
                                        <StopIcon className="w-4 h-4" /> Dừng xe
                                    </button>
                                )}
                            </div>
                        )}
                    </div>

                    {/* Vehicle status */}
                    {vehicle && (
                        <div className="bg-white rounded-2xl border border-gray-200 p-5">
                            <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wide mb-3">Trạng thái xe</h2>
                            <div className="grid grid-cols-3 gap-3 text-center">
                                <div><p className="text-xs text-gray-400">Trạng thái</p><p className={`text-sm font-bold ${statusColor[vehicle.status] || ''}`}>{statusMap[vehicle.status] || vehicle.status}</p></div>
                                <div><p className="text-xs text-gray-400">Vị trí</p><p className="text-sm font-bold text-gray-800">{vehicle.currentPosition}</p></div>
                                <div><p className="text-xs text-gray-400">Pin</p><p className="text-sm font-bold text-gray-800">{vehicle.batteryLevel}%</p></div>
                            </div>
                        </div>
                    )}

                    {/* Nav log */}
                    <div className="bg-white rounded-2xl border border-gray-200 overflow-hidden flex flex-col" style={{ maxHeight: 360 }}>
                        <div className="flex items-center justify-between px-4 py-2.5 border-b bg-gray-50">
                            <span className="text-sm font-medium text-gray-600">Log di chuyển</span>
                            <button onClick={() => setNavLogs([])} className="text-xs text-gray-400 hover:text-red-500">Xoá</button>
                        </div>
                        <div className="flex-1 overflow-y-auto p-3 font-mono text-xs leading-relaxed bg-gray-900 text-green-400">
                            {navLogs.length === 0 && <div className="text-gray-600 text-center py-6">Chờ dữ liệu...</div>}
                            {navLogs.map((log, i) => (
                                <div key={i} className={`py-0.5 ${log.startsWith('🏁') ? 'text-yellow-300 font-bold' : log.startsWith('⏹') ? 'text-red-400' : ''}`}>{log}</div>
                            ))}
                            <div ref={logEndRef} />
                        </div>
                    </div>
                </div>
            </div>
        </div>
    );
}

export default MapPage;
