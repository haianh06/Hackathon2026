import React, { useState, useEffect, useRef } from 'react';
import { WrenchScrewdriverIcon } from '@heroicons/react/24/outline';

function DevPage() {
    const [debug, setDebug] = useState(null);
    const [streaming, setStreaming] = useState(false);
    const [polling, setPolling] = useState(false);
    const [history, setHistory] = useState([]);
    const [streamMode, setStreamMode] = useState('unet'); // 'unet' | 'canny' | 'all'
    const intervalRef = useRef(null);

    const STREAM_URLS = {
        unet: '/camera/lane/stream',
        canny: '/camera/processed/stream?mode=canny',
        all: '/camera/processed/stream?mode=all',
    };
    const STREAM_URL = STREAM_URLS[streamMode];
    const DEBUG_URL = '/camera/lane/debug';

    useEffect(() => {
        if (!polling) { if (intervalRef.current) clearInterval(intervalRef.current); return; }
        const tick = async () => {
            try {
                const res = await fetch(DEBUG_URL);
                const data = await res.json();
                setDebug(data);
                setHistory(prev => { const n = [...prev, { t: Date.now(), c: data.correction || 0 }]; return n.length > 120 ? n.slice(-120) : n; });
            } catch (e) { setDebug({ ready: false, error: e.message }); }
        };
        tick();
        intervalRef.current = setInterval(tick, 250);
        return () => clearInterval(intervalRef.current);
    }, [polling]);

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
        const W = 360, H = 60;
        if (data.length < 2) return <div className="bg-gray-800 rounded-md" style={{ width: W, height: H }} />;
        const pts = data.map((d, i) => `${(i / (data.length - 1)) * W},${H / 2 - (d.c * H / 2)}`).join(' ');
        return (
            <svg width={W} height={H} className="bg-gray-800 rounded-md">
                <line x1={0} y1={H / 2} x2={W} y2={H / 2} stroke="#4a5568" strokeWidth={1} />
                <polyline fill="none" stroke="#60a5fa" strokeWidth={1.5} points={pts} />
            </svg>
        );
    };

    const fmt = (v) => (v === null || v === undefined) ? '—' : typeof v === 'number' ? v.toFixed(1) : String(v);

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
        <div className="max-w-6xl mx-auto">
            <h1 className="text-2xl font-bold text-gray-900 mb-6 flex items-center gap-2">
                <WrenchScrewdriverIcon className="w-7 h-7 text-amber-500" /> Dev — Lane Detection Debug
            </h1>

            <div className="grid grid-cols-1 lg:grid-cols-5 gap-6">
                {/* LEFT: streams (3 cols) */}
                <div className="lg:col-span-3 space-y-4">
                    {/* Lane overlay */}
                    <div className="bg-white rounded-2xl border border-gray-200 overflow-hidden">
                        <div className="flex items-center justify-between px-5 py-3 border-b bg-gray-50">
                            <div className="flex items-center gap-2">
                                <span className="text-sm font-semibold text-gray-600">
                                    {streamMode === 'canny' ? 'Canny Edge' : streamMode === 'all' ? 'Canny + UNet' : 'UNet Lane'}
                                </span>
                                <div className="flex rounded-lg overflow-hidden border border-gray-300">
                                    {[['unet', 'UNet'], ['canny', 'Canny'], ['all', 'All']].map(([mode, label]) => (
                                        <button key={mode} onClick={() => { setStreamMode(mode); if (streaming) { setStreaming(false); setTimeout(() => setStreaming(true), 100); } }}
                                            className={`px-2 py-1 text-xs font-medium ${streamMode === mode ? 'bg-blue-600 text-white' : 'bg-white text-gray-600 hover:bg-gray-100'}`}>
                                            {label}
                                        </button>
                                    ))}
                                </div>
                            </div>
                            <button onClick={() => setStreaming(s => !s)}
                                className={`px-3 py-1.5 text-xs font-medium rounded-lg ${streaming ? 'bg-red-500 text-white' : 'bg-amber-500 text-white'}`}>
                                {streaming ? 'Stop' : 'Start'}
                            </button>
                        </div>
                        <div className="aspect-video bg-black flex items-center justify-center">
                            {streaming ? <img src={STREAM_URL} alt="Lane" className="w-full h-full object-contain" onError={() => setStreaming(false)} />
                                : <span className="text-gray-500 text-sm">Stream tắt</span>}
                        </div>
                    </div>

                    {/* Raw camera */}
                    <div className="bg-white rounded-2xl border border-gray-200 overflow-hidden">
                        <div className="px-5 py-3 border-b bg-gray-50">
                            <span className="text-sm font-semibold text-gray-600">Raw Camera</span>
                        </div>
                        <div className="aspect-video bg-black flex items-center justify-center">
                            {streaming ? <img src="/camera/stream" alt="Raw" className="w-full h-full object-contain" />
                                : <span className="text-gray-500 text-sm">Stream tắt</span>}
                        </div>
                    </div>
                </div>

                {/* RIGHT: data (2 cols) */}
                <div className="lg:col-span-2 space-y-4">
                    {/* Correction gauge */}
                    <div className="bg-white rounded-2xl border border-gray-200 p-5">
                        <div className="flex items-center justify-between mb-3">
                            <span className="text-sm font-semibold text-gray-400 uppercase tracking-wide">Steering</span>
                            <button onClick={() => setPolling(p => !p)}
                                className={`px-3 py-1.5 text-xs font-medium rounded-lg ${polling ? 'bg-red-500 text-white' : 'bg-amber-500 text-white'}`}>
                                {polling ? 'Stop' : 'Poll data'}
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
                        <Sparkline data={history} />
                        <div className="flex justify-between text-[11px] text-gray-400 mt-1">
                            <span>−1.0 (trái)</span>
                            <span>0</span>
                            <span>+1.0 (phải)</span>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    );
}

export default DevPage;
