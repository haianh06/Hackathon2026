import React, { useState, useEffect, useRef, useCallback } from 'react';
import { getMapPoints, getVehicleStatus, findPath, seedMap, returnToBase } from '../services/api';
import socket from '../services/socket';
import DemoMap from '../components/DemoMap';
import {
    MapPinIcon, ArrowPathIcon, PlayIcon, StopIcon,
    WrenchScrewdriverIcon,
} from '@heroicons/react/24/outline';

const STREAM_URLS = {
    unet: '/camera/lane/stream',
    canny: '/camera/processed/stream?mode=canny',
    all: '/camera/processed/stream?mode=all',
};
const DEBUG_URL = '/camera/lane/debug';

function MapPage() {
    // ── Map & Navigation state ──
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

    // ── Dev Debug state ──
    const [showDebug, setShowDebug] = useState(false);
    const [debug, setDebug] = useState(null);
    const [debugPolling, setDebugPolling] = useState(false);
    const [debugHistory, setDebugHistory] = useState([]);
    const [debugStreamMode, setDebugStreamMode] = useState('unet');
    const debugIntervalRef = useRef(null);

    // ── Load map data ──
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

    // ── Debug polling ──
    useEffect(() => {
        if (!debugPolling) { if (debugIntervalRef.current) clearInterval(debugIntervalRef.current); return; }
        const tick = async () => {
            try {
                const res = await fetch(DEBUG_URL);
                const data = await res.json();
                setDebug(data);
                setDebugHistory(prev => { const n = [...prev, { t: Date.now(), c: data.correction || 0 }]; return n.length > 120 ? n.slice(-120) : n; });
            } catch (e) { setDebug({ ready: false, error: e.message }); }
        };
        tick();
        debugIntervalRef.current = setInterval(tick, 250);
        return () => clearInterval(debugIntervalRef.current);
    }, [debugPolling]);

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

    const handleResetMap = async () => { try { const [r] = await Promise.all([seedMap(), returnToBase()]); setPoints(r.data.data); setActivePath(null); setLivePos(null); setIsNavigating(false); setVehicle(prev => prev ? { ...prev, currentPosition: 'S', status: 'idle' } : prev); } catch (e) { console.error(e); } };
    const handleFindPath = async () => { if (!pathFrom || !pathTo) return; try { const r = await findPath(pathFrom, pathTo); setActivePath(r.data.data); } catch (e) { console.error(e); } };
    const handleAutoNavigate = () => { if (!activePath || activePath.length < 2) return; setNavLogs([]); setLivePos(null); socket.emit('auto-navigate', { path: activePath.map(p => ({ pointId: p.pointId, x: p.x, y: p.y })) }); };
    const handleStopNavigation = () => { socket.emit('stop-navigation'); setIsNavigating(false); };

    const statusMap = { idle: 'Sẵn sàng', moving: 'Đang di chuyển', delivering: 'Đang giao', returning: 'Quay về' };
    const statusColor = { idle: 'text-green-600', moving: 'text-yellow-600', delivering: 'text-amber-600', returning: 'text-blue-600' };

    // ── Debug helpers ──
    const fmt = (v) => (v === null || v === undefined) ? '—' : typeof v === 'number' ? v.toFixed(1) : String(v);

    const corrBar = (val) => {
        const clamped = Math.max(-1, Math.min(1, val || 0));
        const pct = ((clamped + 1) / 2) * 100;
        const color = Math.abs(clamped) < 0.15 ? '#22c55e' : Math.abs(clamped) < 0.4 ? '#eab308' : '#ef4444';
        return (
            <div className="relative w-full h-7 bg-gray-800 rounded-md overflow-hidden">
                <div className="absolute left-1/2 top-0 bottom-0 w-0.5 bg-white/40 z-10" />
                <div className="absolute top-0.5 bottom-0.5 w-1.5 rounded z-20 transition-all duration-100" style={{ left: `${pct}%`, marginLeft: -3, background: color }} />
                <span className="absolute left-1.5 top-1 text-gray-400 text-[11px]">◀ Trái</span>
                <span className="absolute right-1.5 top-1 text-gray-400 text-[11px]">Phải ▶</span>
            </div>
        );
    };

    const Sparkline = ({ data }) => {
        const W = 320, H = 55;
        if (data.length < 2) return <div className="bg-gray-800 rounded-md" style={{ width: W, height: H }} />;
        const pts = data.map((d, i) => `${(i / (data.length - 1)) * W},${H / 2 - (d.c * H / 2)}`).join(' ');
        return (
            <svg width={W} height={H} className="bg-gray-800 rounded-md">
                <line x1={0} y1={H / 2} x2={W} y2={H / 2} stroke="#4a5568" strokeWidth={1} />
                <polyline fill="none" stroke="#60a5fa" strokeWidth={1.5} points={pts} />
            </svg>
        );
    };

    const debugRows = [
        ['Model ready', debug ? (debug.ready ? '✅ Yes' : '❌ No') : '—'],
        ['Borders found', debug?.borders_found ?? '—', debug?.borders_found === 2 ? 'text-green-500' : debug?.borders_found === 1 ? 'text-yellow-500' : 'text-red-500'],
        ['Confidence', debug?.confidence != null ? (debug.confidence * 100).toFixed(0) + '%' : '—'],
        ['Frame center X', fmt(debug?.frame_cx) + ' px'],
        ['Lane center X', fmt(debug?.lane_cx) + ' px'],
        ['Left border', fmt(debug?.left_edge) + ' px', 'text-red-400'],
        ['Right border', fmt(debug?.right_edge) + ' px', 'text-blue-400'],
        ['Gap center', fmt(debug?.gap_center) + ' px'],
        ['Lane width', fmt(debug?.lane_width) + ' px'],
        ['EMA lane width', fmt(debug?.ema_lane_width) + ' px'],
        ['ROI top', fmt(debug?.roi_top) + ' px'],
        ['Raw correction', fmt(debug?.raw_correction), 'font-mono'],
        ['EMA correction', fmt(debug?.correction), 'font-mono font-bold'],
    ];

    return (
        <div className="max-w-7xl mx-auto">
            <div className="flex items-center justify-between mb-6">
                <h1 className="text-2xl font-bold text-gray-900 flex items-center gap-2">
                    <MapPinIcon className="w-7 h-7 text-amber-500" /> Bản đồ & Xe tự hành
                </h1>
                <button onClick={() => setShowDebug(!showDebug)}
                    className={`px-3 py-1.5 text-xs rounded-lg border transition flex items-center gap-1 ${showDebug ? 'bg-amber-500 text-white border-amber-500' : 'bg-gray-50 text-gray-600 border-gray-200 hover:bg-gray-100'}`}>
                    <WrenchScrewdriverIcon className="w-3.5 h-3.5" /> Dev Debug
                </button>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
                {/* ═══ LEFT: Map + Camera (7 cols) ═══ */}
                <div className={`${showDebug ? 'lg:col-span-5' : 'lg:col-span-7'} space-y-4`}>
                    {/* Map visualization */}
                    <div className="bg-white rounded-2xl border border-gray-200 p-4">
                        <DemoMap points={points} vehiclePosition={vehicle?.currentPosition} activePath={activePath} livePos={livePos} />
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

                {/* ═══ MIDDLE: Controls + Log (5 cols or 5+debug) ═══ */}
                <div className={`${showDebug ? 'lg:col-span-3' : 'lg:col-span-5'} space-y-4`}>
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

                {/* ═══ RIGHT: Dev Debug Panel (4 cols, togglable) ═══ */}
                {showDebug && (
                    <div className="lg:col-span-4 space-y-4">
                        {/* Lane detection overlay stream */}
                        <div className="bg-white rounded-2xl border border-gray-200 overflow-hidden">
                            <div className="flex items-center justify-between px-4 py-2.5 border-b bg-gray-50">
                                <div className="flex items-center gap-2">
                                    <span className="text-sm font-semibold text-gray-600">
                                        {debugStreamMode === 'canny' ? 'Canny Edge' : debugStreamMode === 'all' ? 'Canny + UNet' : 'UNet Lane'}
                                    </span>
                                    <div className="flex rounded-lg overflow-hidden border border-gray-300">
                                        {[['unet', 'UNet'], ['canny', 'Canny'], ['all', 'All']].map(([mode, label]) => (
                                            <button key={mode} onClick={() => setDebugStreamMode(mode)}
                                                className={`px-2 py-1 text-xs font-medium ${debugStreamMode === mode ? 'bg-blue-600 text-white' : 'bg-white text-gray-600 hover:bg-gray-100'}`}>
                                                {label}
                                            </button>
                                        ))}
                                    </div>
                                </div>
                            </div>
                            <div className="aspect-video bg-black flex items-center justify-center">
                                {debugPolling ? <img src={STREAM_URLS[debugStreamMode]} alt="Lane" className="w-full h-full object-contain" onError={() => { }} />
                                    : <span className="text-gray-500 text-sm">Bấm "Poll" để bắt đầu</span>}
                            </div>
                        </div>

                        {/* Steering gauge */}
                        <div className="bg-white rounded-2xl border border-gray-200 p-5">
                            <div className="flex items-center justify-between mb-3">
                                <span className="text-sm font-semibold text-gray-400 uppercase tracking-wide">Steering</span>
                                <button onClick={() => setDebugPolling(p => !p)}
                                    className={`px-3 py-1.5 text-xs font-medium rounded-lg ${debugPolling ? 'bg-red-500 text-white' : 'bg-amber-500 text-white'}`}>
                                    {debugPolling ? 'Stop' : 'Poll data'}
                                </button>
                            </div>
                            <div className="text-center font-mono text-4xl font-bold mb-2" style={{ color: debug && Math.abs(debug.correction) > 0.15 ? '#ef4444' : '#22c55e' }}>
                                {debug ? (debug.correction > 0 ? '+' : '') + debug.correction.toFixed(3) : '—'}
                            </div>
                            {corrBar(debug?.correction)}
                            <p className="text-center text-xs text-gray-400 mt-2">
                                {debug?.correction < -0.15 ? '⬅ Lệch trái' : debug?.correction > 0.15 ? '➡ Lệch phải' : '✅ Giữa làn'}
                            </p>
                        </div>

                        {/* Debug table */}
                        <div className="bg-white rounded-2xl border border-gray-200 p-5">
                            <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wide mb-3">Debug Data</h2>
                            <div className="space-y-1.5">
                                {debugRows.map(([label, val, cls], i) => (
                                    <div key={i} className="flex justify-between text-sm">
                                        <span className="text-gray-500">{label}</span>
                                        <span className={`font-medium ${cls || 'text-gray-800'}`}>{val}</span>
                                    </div>
                                ))}
                            </div>
                        </div>

                        {/* Sparkline */}
                        <div className="bg-white rounded-2xl border border-gray-200 p-5">
                            <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wide mb-3">Correction History</h2>
                            <Sparkline data={debugHistory} />
                            <div className="flex justify-between text-[11px] text-gray-400 mt-1">
                                <span>−1.0 (trái)</span>
                                <span>0</span>
                                <span>+1.0 (phải)</span>
                            </div>
                        </div>
                    </div>
                )}
            </div>
        </div>
    );
}

export default MapPage;
