import React, { useState, useEffect, useRef, useCallback } from 'react';
import socket from '../services/socket';
import { WrenchScrewdriverIcon } from '@heroicons/react/24/outline';

const NEUTRAL = 1500;
const MIN_PULSE = 1000;
const MAX_PULSE = 2000;

export default function MotorCalibrationPage() {
    const [leftPulse, setLeftPulse] = useState(NEUTRAL);
    const [rightPulse, setRightPulse] = useState(NEUTRAL);
    const [chartData, setChartData] = useState([]);
    const [running, setRunning] = useState(false);
    const [testType, setTestType] = useState(null);
    const chartRef = useRef(null);
    const maxPoints = 300;

    // Sweep params
    const [sweepStart, setSweepStart] = useState(1300);
    const [sweepEnd, setSweepEnd] = useState(1700);
    const [sweepStep, setSweepStep] = useState(5);
    const [sweepHold, setSweepHold] = useState(50);
    const [sweepPin, setSweepPin] = useState('both');

    // Step params
    const [stepTarget, setStepTarget] = useState(1800);
    const [stepDuration, setStepDuration] = useState(3);
    const [stepPin, setStepPin] = useState('both');

    // Deadband test params
    const [dbStep, setDbStep] = useState(1);
    const [dbHold, setDbHold] = useState(100);
    const [dbMaxOffset, setDbMaxOffset] = useState(150);
    const [dbPin, setDbPin] = useState('both');

    useEffect(() => {
        socket.emit('join-room', 'admin');

        const handleData = (data) => {
            setChartData(prev => {
                const next = [...prev, data];
                return next.length > maxPoints ? next.slice(-maxPoints) : next;
            });
            if (data.type === 'sweep_done' || data.type === 'deadband_done' || data.phase === 'done') {
                setRunning(false);
                setTestType(null);
            }
        };

        socket.on('motor-calibrate-data', handleData);
        return () => {
            socket.off('motor-calibrate-data', handleData);
        };
    }, []);

    const setManualPWM = useCallback((pin, value) => {
        socket.emit('motor-calibrate-set', { pin, pulse_us: value });
        setChartData(prev => {
            const next = [...prev, {
                type: 'manual', pin, pulse_us: value,
                time: prev.length > 0 ? (prev[prev.length - 1].time || 0) + 0.1 : 0
            }];
            return next.length > maxPoints ? next.slice(-maxPoints) : next;
        });
    }, []);

    const stopAll = useCallback(() => {
        socket.emit('motor-calibrate-stop');
        setRunning(false);
        setTestType(null);
        setLeftPulse(NEUTRAL);
        setRightPulse(NEUTRAL);
    }, []);

    const startSweep = useCallback(() => {
        setChartData([]);
        setRunning(true);
        setTestType('sweep');
        socket.emit('motor-calibrate-sweep', {
            pin: sweepPin, start_us: sweepStart, end_us: sweepEnd,
            step_us: sweepStep, hold_ms: sweepHold
        });
    }, [sweepPin, sweepStart, sweepEnd, sweepStep, sweepHold]);

    const startStep = useCallback(() => {
        setChartData([]);
        setRunning(true);
        setTestType('step');
        socket.emit('motor-calibrate-step', {
            pin: stepPin, target_us: stepTarget, duration_s: stepDuration
        });
    }, [stepPin, stepTarget, stepDuration]);

    const startDeadband = useCallback(() => {
        setChartData([]);
        setRunning(true);
        setTestType('deadband');
        socket.emit('motor-calibrate-deadband', {
            pin: dbPin, step_us: dbStep, hold_ms: dbHold, max_offset: dbMaxOffset
        });
    }, [dbPin, dbStep, dbHold, dbMaxOffset]);

    // Chart rendering
    const Chart = ({ data, height = 220 }) => {
        const W = 700;
        const H = height;
        const padding = { top: 20, right: 20, bottom: 30, left: 55 };
        const plotW = W - padding.left - padding.right;
        const plotH = H - padding.top - padding.bottom;

        if (data.length < 2) {
            return (
                <div className="bg-gray-900 rounded-xl flex items-center justify-center" style={{ width: '100%', height: H }}>
                    <span className="text-gray-500 text-sm">Chưa có dữ liệu — chạy test để xem biểu đồ</span>
                </div>
            );
        }

        const times = data.map(d => d.time || 0);
        const pulses = data.map(d => d.pulse_us || NEUTRAL);
        const minT = Math.min(...times);
        const maxT = Math.max(...times);
        const minP = Math.min(...pulses, NEUTRAL - 50);
        const maxP = Math.max(...pulses, NEUTRAL + 50);
        const rangeT = maxT - minT || 1;
        const rangeP = maxP - minP || 1;

        const scaleX = (t) => padding.left + ((t - minT) / rangeT) * plotW;
        const scaleY = (p) => padding.top + plotH - ((p - minP) / rangeP) * plotH;

        const points = data.map(d => `${scaleX(d.time || 0)},${scaleY(d.pulse_us || NEUTRAL)}`).join(' ');

        // Neutral line
        const neutralY = scaleY(NEUTRAL);

        // Y-axis ticks
        const yTicks = [];
        const yStep = rangeP > 200 ? 100 : rangeP > 100 ? 50 : 20;
        for (let v = Math.ceil(minP / yStep) * yStep; v <= maxP; v += yStep) {
            yTicks.push(v);
        }

        // X-axis ticks
        const xTicks = [];
        const xStep = rangeT > 10 ? 2 : rangeT > 5 ? 1 : 0.5;
        for (let v = Math.ceil(minT / xStep) * xStep; v <= maxT; v += xStep) {
            xTicks.push(v);
        }

        // Color by phase
        const phaseColor = (d) => {
            if (d.phase === 'forward') return '#60a5fa';
            if (d.phase === 'reverse') return '#f59e0b';
            if (d.phase === 'active') return '#34d399';
            if (d.phase === 'idle') return '#9ca3af';
            return '#60a5fa';
        };

        // Build segments with different colors
        const segments = [];
        let currentPhase = data[0].phase;
        let currentSegment = [data[0]];
        for (let i = 1; i < data.length; i++) {
            if (data[i].phase !== currentPhase) {
                segments.push({ phase: currentPhase, points: currentSegment });
                currentPhase = data[i].phase;
                currentSegment = [data[i - 1], data[i]];
            } else {
                currentSegment.push(data[i]);
            }
        }
        segments.push({ phase: currentPhase, points: currentSegment });

        return (
            <svg viewBox={`0 0 ${W} ${H}`} className="w-full bg-gray-900 rounded-xl" preserveAspectRatio="xMidYMid meet">
                {/* Grid */}
                {yTicks.map(v => (
                    <g key={`y-${v}`}>
                        <line x1={padding.left} y1={scaleY(v)} x2={W - padding.right} y2={scaleY(v)}
                            stroke="#374151" strokeWidth={0.5} />
                        <text x={padding.left - 5} y={scaleY(v) + 4} textAnchor="end"
                            fill="#6b7280" fontSize="10">{v}</text>
                    </g>
                ))}
                {xTicks.map(v => (
                    <g key={`x-${v}`}>
                        <line x1={scaleX(v)} y1={padding.top} x2={scaleX(v)} y2={H - padding.bottom}
                            stroke="#374151" strokeWidth={0.5} />
                        <text x={scaleX(v)} y={H - padding.bottom + 15} textAnchor="middle"
                            fill="#6b7280" fontSize="10">{v.toFixed(1)}s</text>
                    </g>
                ))}

                {/* Neutral line */}
                <line x1={padding.left} y1={neutralY} x2={W - padding.right} y2={neutralY}
                    stroke="#ef4444" strokeWidth={1} strokeDasharray="4,3" opacity={0.7} />
                <text x={W - padding.right + 2} y={neutralY + 3} fill="#ef4444" fontSize="9">1500µs</text>

                {/* Data line segments */}
                {segments.map((seg, i) => {
                    const pts = seg.points.map(d => `${scaleX(d.time || 0)},${scaleY(d.pulse_us || NEUTRAL)}`).join(' ');
                    return (
                        <polyline key={i} fill="none" stroke={phaseColor(seg.points[0])}
                            strokeWidth={2} points={pts} />
                    );
                })}

                {/* Axis labels */}
                <text x={padding.left + plotW / 2} y={H - 3} textAnchor="middle" fill="#9ca3af" fontSize="10">
                    Thời gian (s)
                </text>
                <text x={12} y={padding.top + plotH / 2} textAnchor="middle" fill="#9ca3af" fontSize="10"
                    transform={`rotate(-90, 12, ${padding.top + plotH / 2})`}>
                    Xung PWM (µs)
                </text>
            </svg>
        );
    };

    return (
        <div className="max-w-6xl mx-auto">
            <h1 className="text-2xl font-bold text-gray-900 mb-6 flex items-center gap-2">
                <WrenchScrewdriverIcon className="w-7 h-7 text-amber-500" />
                Đo & Hiệu chỉnh Động cơ
            </h1>

            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                {/* LEFT: Chart (2 cols) */}
                <div className="lg:col-span-2 space-y-4">
                    {/* Real-time chart */}
                    <div className="bg-white rounded-2xl border border-gray-200 overflow-hidden">
                        <div className="flex items-center justify-between px-5 py-3 border-b bg-gray-50">
                            <span className="text-sm font-semibold text-gray-600">
                                Biểu đồ xung PWM theo thời gian
                            </span>
                            <div className="flex items-center gap-2">
                                {running && (
                                    <span className="flex items-center gap-1 text-xs text-green-600">
                                        <span className="w-2 h-2 bg-green-500 rounded-full animate-pulse" />
                                        Đang chạy
                                    </span>
                                )}
                                <button onClick={() => setChartData([])}
                                    className="text-xs text-gray-400 hover:text-amber-600">Xóa</button>
                            </div>
                        </div>
                        <div className="p-4" ref={chartRef}>
                            <Chart data={chartData} height={280} />
                        </div>
                        {/* Legend */}
                        <div className="px-5 pb-3 flex items-center gap-4 text-xs text-gray-500">
                            <span className="flex items-center gap-1">
                                <span className="w-3 h-0.5 bg-blue-400 inline-block" /> Sweep lên
                            </span>
                            <span className="flex items-center gap-1">
                                <span className="w-3 h-0.5 bg-amber-400 inline-block" /> Sweep xuống
                            </span>
                            <span className="flex items-center gap-1">
                                <span className="w-3 h-0.5 bg-green-400 inline-block" /> Step active
                            </span>
                            <span className="flex items-center gap-1">
                                <span className="w-3 h-0.5 bg-red-400 inline-block" strokeDasharray="4,3" /> Neutral (1500µs)
                            </span>
                        </div>
                    </div>

                    {/* Data summary */}
                    {chartData.length > 0 && (
                        <div className="bg-white rounded-2xl border border-gray-200 p-5">
                            <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wide mb-3">
                                Thống kê
                            </h2>
                            <div className="grid grid-cols-4 gap-4 text-center">
                                <div>
                                    <p className="text-2xl font-bold text-gray-800">
                                        {chartData.length}
                                    </p>
                                    <p className="text-xs text-gray-400">Mẫu dữ liệu</p>
                                </div>
                                <div>
                                    <p className="text-2xl font-bold text-blue-600">
                                        {Math.min(...chartData.map(d => d.pulse_us || NEUTRAL))}
                                    </p>
                                    <p className="text-xs text-gray-400">Min (µs)</p>
                                </div>
                                <div>
                                    <p className="text-2xl font-bold text-amber-600">
                                        {Math.max(...chartData.map(d => d.pulse_us || NEUTRAL))}
                                    </p>
                                    <p className="text-xs text-gray-400">Max (µs)</p>
                                </div>
                                <div>
                                    <p className="text-2xl font-bold text-green-600">
                                        {(chartData[chartData.length - 1]?.time || 0).toFixed(1)}s
                                    </p>
                                    <p className="text-xs text-gray-400">Thời gian</p>
                                </div>
                            </div>
                        </div>
                    )}
                </div>

                {/* RIGHT: Controls (1 col) */}
                <div className="space-y-4">
                    {/* Manual control */}
                    <div className="bg-white rounded-2xl border border-gray-200 p-5">
                        <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wide mb-4">
                            Điều khiển thủ công
                        </h2>

                        {/* Left servo */}
                        <div className="mb-4">
                            <div className="flex items-center justify-between mb-1">
                                <label className="text-sm font-medium text-gray-700">Servo Trái (GPIO 12)</label>
                                <span className="text-sm font-mono text-amber-600 font-bold">{leftPulse}µs</span>
                            </div>
                            <input type="range" min={MIN_PULSE} max={MAX_PULSE} step={10} value={leftPulse}
                                onChange={(e) => { const v = parseInt(e.target.value); setLeftPulse(v); setManualPWM('left', v); }}
                                className="w-full accent-amber-500" />
                            <div className="flex justify-between text-[10px] text-gray-400 mt-0.5">
                                <span>{MIN_PULSE}</span>
                                <span className="text-red-400">1500 (neutral)</span>
                                <span>{MAX_PULSE}</span>
                            </div>
                        </div>

                        {/* Right servo */}
                        <div className="mb-4">
                            <div className="flex items-center justify-between mb-1">
                                <label className="text-sm font-medium text-gray-700">Servo Phải (GPIO 13)</label>
                                <span className="text-sm font-mono text-blue-600 font-bold">{rightPulse}µs</span>
                            </div>
                            <input type="range" min={MIN_PULSE} max={MAX_PULSE} step={10} value={rightPulse}
                                onChange={(e) => { const v = parseInt(e.target.value); setRightPulse(v); setManualPWM('right', v); }}
                                className="w-full accent-blue-500" />
                            <div className="flex justify-between text-[10px] text-gray-400 mt-0.5">
                                <span>{MIN_PULSE}</span>
                                <span className="text-red-400">1500 (neutral)</span>
                                <span>{MAX_PULSE}</span>
                            </div>
                        </div>

                        <button onClick={stopAll}
                            className="w-full py-2 bg-red-500 text-white text-sm font-medium rounded-lg hover:bg-red-600 transition">
                            ⏹ Dừng tất cả
                        </button>
                    </div>

                    {/* Sweep test */}
                    <div className="bg-white rounded-2xl border border-gray-200 p-5">
                        <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wide mb-3">
                            🔄 Sweep Test
                        </h2>
                        <p className="text-xs text-gray-400 mb-3">
                            Quét PWM từ giá trị bắt đầu đến kết thúc rồi quay lại. Dùng để tìm vùng chết (deadband) và điểm ổn định.
                        </p>

                        <div className="grid grid-cols-2 gap-2 mb-3">
                            <div>
                                <label className="text-xs text-gray-500">Bắt đầu (µs)</label>
                                <input type="number" value={sweepStart} onChange={e => setSweepStart(+e.target.value)}
                                    className="w-full border rounded-lg px-2 py-1.5 text-sm" disabled={running} />
                            </div>
                            <div>
                                <label className="text-xs text-gray-500">Kết thúc (µs)</label>
                                <input type="number" value={sweepEnd} onChange={e => setSweepEnd(+e.target.value)}
                                    className="w-full border rounded-lg px-2 py-1.5 text-sm" disabled={running} />
                            </div>
                            <div>
                                <label className="text-xs text-gray-500">Bước (µs)</label>
                                <input type="number" value={sweepStep} onChange={e => setSweepStep(+e.target.value)}
                                    className="w-full border rounded-lg px-2 py-1.5 text-sm" disabled={running} />
                            </div>
                            <div>
                                <label className="text-xs text-gray-500">Giữ (ms)</label>
                                <input type="number" value={sweepHold} onChange={e => setSweepHold(+e.target.value)}
                                    className="w-full border rounded-lg px-2 py-1.5 text-sm" disabled={running} />
                            </div>
                        </div>

                        <div className="mb-3">
                            <label className="text-xs text-gray-500">Motor</label>
                            <select value={sweepPin} onChange={e => setSweepPin(e.target.value)}
                                className="w-full border rounded-lg px-2 py-1.5 text-sm" disabled={running}>
                                <option value="both">Cả hai</option>
                                <option value="left">Trái (GPIO 12)</option>
                                <option value="right">Phải (GPIO 13)</option>
                            </select>
                        </div>

                        <button onClick={startSweep} disabled={running}
                            className="w-full py-2 bg-blue-500 text-white text-sm font-medium rounded-lg hover:bg-blue-600 transition disabled:opacity-50">
                            {running && testType === 'sweep' ? '⏳ Đang chạy...' : '▶ Chạy Sweep'}
                        </button>
                    </div>

                    {/* Step test */}
                    <div className="bg-white rounded-2xl border border-gray-200 p-5">
                        <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wide mb-3">
                            📈 Step Response Test
                        </h2>
                        <p className="text-xs text-gray-400 mb-3">
                            Nhảy từ 1500µs (neutral) đến xung mục tiêu và giữ. Quan sát motor phản hồi thế nào khi nhận xung đột ngột.
                        </p>

                        <div className="grid grid-cols-2 gap-2 mb-3">
                            <div>
                                <label className="text-xs text-gray-500">Xung mục tiêu (µs)</label>
                                <input type="number" value={stepTarget} onChange={e => setStepTarget(+e.target.value)}
                                    className="w-full border rounded-lg px-2 py-1.5 text-sm" disabled={running} />
                            </div>
                            <div>
                                <label className="text-xs text-gray-500">Thời gian (s)</label>
                                <input type="number" value={stepDuration} onChange={e => setStepDuration(+e.target.value)}
                                    className="w-full border rounded-lg px-2 py-1.5 text-sm" disabled={running} />
                            </div>
                        </div>

                        <div className="mb-3">
                            <label className="text-xs text-gray-500">Motor</label>
                            <select value={stepPin} onChange={e => setStepPin(e.target.value)}
                                className="w-full border rounded-lg px-2 py-1.5 text-sm" disabled={running}>
                                <option value="both">Cả hai</option>
                                <option value="left">Trái (GPIO 12)</option>
                                <option value="right">Phải (GPIO 13)</option>
                            </select>
                        </div>

                        <button onClick={startStep} disabled={running}
                            className="w-full py-2 bg-green-500 text-white text-sm font-medium rounded-lg hover:bg-green-600 transition disabled:opacity-50">
                            {running && testType === 'step' ? '⏳ Đang chạy...' : '▶ Chạy Step Test'}
                        </button>
                    </div>

                    {/* Deadband test */}
                    <div className="bg-white rounded-2xl border border-gray-200 p-5">
                        <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wide mb-3">
                            🎯 Dead Band Test
                        </h2>
                        <p className="text-xs text-gray-400 mb-3">
                            Quét từ 1500µs ra ngoài để tìm chính xác vùng chết (dead zone) — giá trị µs mà motor bắt đầu quay.
                            Quan sát motor khi nào bắt đầu xoay để xác định ranh giới dead band.
                        </p>

                        <div className="grid grid-cols-2 gap-2 mb-3">
                            <div>
                                <label className="text-xs text-gray-500">Bước (µs)</label>
                                <input type="number" value={dbStep} onChange={e => setDbStep(+e.target.value)}
                                    className="w-full border rounded-lg px-2 py-1.5 text-sm" disabled={running} />
                            </div>
                            <div>
                                <label className="text-xs text-gray-500">Giữ (ms)</label>
                                <input type="number" value={dbHold} onChange={e => setDbHold(+e.target.value)}
                                    className="w-full border rounded-lg px-2 py-1.5 text-sm" disabled={running} />
                            </div>
                            <div>
                                <label className="text-xs text-gray-500">Offset tối đa (µs)</label>
                                <input type="number" value={dbMaxOffset} onChange={e => setDbMaxOffset(+e.target.value)}
                                    className="w-full border rounded-lg px-2 py-1.5 text-sm" disabled={running} />
                            </div>
                            <div>
                                <label className="text-xs text-gray-500">Motor</label>
                                <select value={dbPin} onChange={e => setDbPin(e.target.value)}
                                    className="w-full border rounded-lg px-2 py-1.5 text-sm" disabled={running}>
                                    <option value="both">Cả hai</option>
                                    <option value="left">Trái (GPIO 12)</option>
                                    <option value="right">Phải (GPIO 13)</option>
                                </select>
                            </div>
                        </div>

                        <button onClick={startDeadband} disabled={running}
                            className="w-full py-2 bg-purple-500 text-white text-sm font-medium rounded-lg hover:bg-purple-600 transition disabled:opacity-50">
                            {running && testType === 'deadband' ? '⏳ Đang đo...' : '▶ Đo Dead Band'}
                        </button>
                    </div>

                    {/* Info card */}
                    <div className="bg-amber-50 rounded-2xl border border-amber-200 p-4">
                        <h3 className="text-sm font-semibold text-amber-700 mb-2">💡 Hướng dẫn</h3>
                        <ul className="text-xs text-amber-600 space-y-1">
                            <li>• <strong>1500µs</strong> = Neutral (motor dừng)</li>
                            <li>• <strong>&gt;1500µs</strong> = Quay tiến (cả 2 bánh)</li>
                            <li>• <strong>&lt;1500µs</strong> = Quay lùi (cả 2 bánh)</li>
                            <li>• Servo trái được tự động đảo xung (gắn đối xứng gương)</li>
                            <li>• <strong>Sweep</strong>: Quét dải xung để tìm vùng chết (mặc định 5µs/bước, 50ms/bước)</li>
                            <li>• <strong>Step</strong>: Nhảy đột ngột để đo đáp ứng</li>
                            <li>• <strong>Dead Band</strong>: Quét từ neutral ra ngoài 1µs/bước để tìm ranh giới chính xác</li>
                            <li>• Quan sát motor vật lý kết hợp biểu đồ</li>
                        </ul>
                    </div>
                </div>
            </div>
        </div>
    );
}
