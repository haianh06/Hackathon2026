import React, { useState, useEffect, useRef, useCallback } from 'react';
import socket from '../services/socket';
import { getHardwareStatus, startHardwareDaemon, stopHardwareDaemon } from '../services/api';
import {
    ArrowUpIcon, ArrowDownIcon, ArrowLeftIcon, ArrowRightIcon,
    StopIcon, CpuChipIcon, SignalIcon, SignalSlashIcon
} from '@heroicons/react/24/solid';

const MIN_CMD_DURATION = 300;

export default function StaffControlPage() {
    const [activeCmd, setActiveCmd] = useState(null);
    const [motorStatus, setMotorStatus] = useState(null);
    const [hwStatus, setHwStatus] = useState(null);
    const cmdStartTime = useRef(0);
    const stopTimer = useRef(null);

    useEffect(() => {
        loadHwStatus();
        socket.on('motor-status-update', setMotorStatus);
        socket.on('hardware-status-update', setHwStatus);
        return () => {
            socket.off('motor-status-update');
            socket.off('hardware-status-update');
            if (stopTimer.current) clearTimeout(stopTimer.current);
        };
    }, []);

    const loadHwStatus = async () => {
        try {
            const res = await getHardwareStatus();
            setHwStatus(res.data.data);
        } catch (e) { console.error(e); }
    };

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
    }, [sendCommand, stopMotor]);

    const btnClass = (cmd) =>
        `w-16 h-16 rounded-xl flex items-center justify-center transition-all duration-100 select-none ${activeCmd === cmd
            ? 'bg-amber-500 text-white shadow-lg scale-95'
            : 'bg-white text-gray-700 border border-gray-200 hover:bg-amber-50 hover:border-amber-300 shadow-sm'
        }`;

    return (
        <div className="max-w-2xl mx-auto">
            <h1 className="text-2xl font-bold text-gray-900 mb-6">Điều khiển xe thủ công</h1>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
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

                    <p className="text-xs text-gray-400 mt-4">W/A/S/D hoặc Arrow keys &middot; Space = Dừng</p>
                </div>

                {/* Hardware info panel */}
                <div className="space-y-4">
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
