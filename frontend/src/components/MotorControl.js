import React, { useState, useEffect, useRef, useCallback } from 'react';
import socket from '../services/socket';

// Minimum time (ms) a command stays active before stop is allowed
const MIN_CMD_DURATION = 300;

function MotorControl() {
    const [activeCmd, setActiveCmd] = useState(null);
    const [motorStatus, setMotorStatus] = useState(null);
    const cmdStartTime = useRef(0);
    const stopTimer = useRef(null);

    useEffect(() => {
        socket.on('motor-status-update', (status) => {
            setMotorStatus(status);
        });
        return () => {
            socket.off('motor-status-update');
            if (stopTimer.current) clearTimeout(stopTimer.current);
        };
    }, []);

    const sendCommand = useCallback((command) => {
        if (stopTimer.current) {
            clearTimeout(stopTimer.current);
            stopTimer.current = null;
        }
        socket.emit('motor-control', { command, speed: 50 });
        setActiveCmd(command);
        cmdStartTime.current = Date.now();
    }, []);

    const stopMotor = useCallback(() => {
        const elapsed = Date.now() - cmdStartTime.current;
        if (elapsed < MIN_CMD_DURATION) {
            // Delay stop so servo has at least MIN_CMD_DURATION ms to move
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

    // Prevent touch+mouse double-fire
    const handlePointerDown = useCallback((command) => (e) => {
        e.preventDefault();
        sendCommand(command);
    }, [sendCommand]);

    const handlePointerUp = useCallback((e) => {
        e.preventDefault();
        stopMotor();
    }, [stopMotor]);

    // Keyboard support
    useEffect(() => {
        const handleKeyDown = (e) => {
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
        const handleKeyUp = (e) => {
            const keys = ['w', 's', 'a', 'd', 'arrowup', 'arrowdown', 'arrowleft', 'arrowright'];
            if (keys.includes(e.key.toLowerCase())) stopMotor();
        };
        window.addEventListener('keydown', handleKeyDown);
        window.addEventListener('keyup', handleKeyUp);
        return () => {
            window.removeEventListener('keydown', handleKeyDown);
            window.removeEventListener('keyup', handleKeyUp);
        };
    }, [sendCommand, stopMotor]);

    return (
        <div className="motor-control">
            <h4>🎮 Điều khiển xe (2 Servo)</h4>
            {motorStatus && (
                <div className="motor-info" style={{ fontSize: '0.75rem', marginBottom: 8, color: '#aaa' }}>
                    Driver: {motorStatus.driver} | Port: {motorStatus.port || 'N/A'} |
                    Status: <span style={{ color: motorStatus.connected ? '#4caf50' : '#f44336' }}>
                        {motorStatus.connected ? '✅ Connected' : '❌ Disconnected'}
                    </span>
                </div>
            )}
            <div className="motor-pad">
                <div className="motor-row">
                    <div className="motor-spacer" />
                    <button
                        className={`motor-btn ${activeCmd === 'forward' ? 'active' : ''}`}
                        onPointerDown={handlePointerDown('forward')}
                        onPointerUp={handlePointerUp}
                        onPointerLeave={handlePointerUp}
                        title="Tiến (W / ↑)"
                    >
                        ▲
                    </button>
                    <div className="motor-spacer" />
                </div>
                <div className="motor-row">
                    <button
                        className={`motor-btn ${activeCmd === 'left' ? 'active' : ''}`}
                        onPointerDown={handlePointerDown('left')}
                        onPointerUp={handlePointerUp}
                        onPointerLeave={handlePointerUp}
                        title="Rẽ trái (A / ←) - L↺ R↻"
                    >
                        ◀
                    </button>
                    <button
                        className="motor-btn stop-btn"
                        onClick={stopMotor}
                        title="Dừng (Space)"
                    >
                        ⏹
                    </button>
                    <button
                        className={`motor-btn ${activeCmd === 'right' ? 'active' : ''}`}
                        onPointerDown={handlePointerDown('right')}
                        onPointerUp={handlePointerUp}
                        onPointerLeave={handlePointerUp}
                        title="Rẽ phải (D / →) - L↻ R↺"
                    >
                        ▶
                    </button>
                </div>
                <div className="motor-row">
                    <div className="motor-spacer" />
                    <button
                        className={`motor-btn ${activeCmd === 'backward' ? 'active' : ''}`}
                        onPointerDown={handlePointerDown('backward')}
                        onPointerUp={handlePointerUp}
                        onPointerLeave={handlePointerUp}
                        title="Lùi (S / ↓)"
                    >
                        ▼
                    </button>
                    <div className="motor-spacer" />
                </div>
            </div>
            <p style={{ fontSize: '0.7rem', color: '#888', margin: '8px 0 0' }}>
                Phím: W/A/S/D hoặc ↑←↓→ | Space = Dừng
            </p>
        </div>
    );
}

export default MotorControl;
