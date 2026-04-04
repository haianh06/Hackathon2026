import React, { useState, useEffect } from 'react';
import axios from 'axios';
import socket from '../services/socket';

const API_BASE = process.env.REACT_APP_API_URL || 'http://localhost:5000/api';

function HardwareStatus() {
    const [hwStatus, setHwStatus] = useState(null);
    const [detected, setDetected] = useState(null);
    const [loading, setLoading] = useState(false);

    useEffect(() => {
        loadStatus();

        socket.on('hardware-status-update', (status) => {
            setHwStatus(status);
        });

        return () => {
            socket.off('hardware-status-update');
        };
    }, []);

    const loadStatus = async () => {
        try {
            const res = await axios.get(`${API_BASE}/hardware/status`);
            setHwStatus(res.data.data);
        } catch (err) {
            console.error('Hardware status error:', err);
        }
    };

    const detectHardware = async () => {
        setLoading(true);
        try {
            const res = await axios.get(`${API_BASE}/hardware/detect`);
            setDetected(res.data.data);
        } catch (err) {
            console.error('Detect error:', err);
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="hardware-status-panel">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
                <h4>🔌 Phần cứng</h4>
                <div style={{ display: 'flex', gap: '0.5rem' }}>
                    <button className="btn btn-primary btn-sm" onClick={loadStatus}>🔄</button>
                    <button className="btn btn-primary btn-sm" onClick={detectHardware} disabled={loading}>
                        {loading ? '⏳' : '🔍'} Scan
                    </button>
                </div>
            </div>

            {hwStatus && (
                <div className="hw-info-grid">
                    <div className="hw-info-item">
                        <span className="hw-label">Daemon</span>
                        <span className={`hw-value ${hwStatus.daemon_running ? 'ok' : 'err'}`}>
                            {hwStatus.daemon_running ? '🟢 Running' : '🔴 Stopped'}
                        </span>
                    </div>
                    <div className="hw-info-item">
                        <span className="hw-label">Motor</span>
                        <span className="hw-value">
                            {hwStatus.motor?.driver === 'serial' ? '🔌' : '🔧'} {hwStatus.motor?.type || hwStatus.motor?.driver}
                            {' - '}
                            <span style={{ color: hwStatus.motor?.connected ? '#4caf50' : hwStatus.motor?.status === 'idle' ? '#aaa' : '#ff9800' }}>
                                {hwStatus.motor?.connected ? hwStatus.motor.status : hwStatus.motor?.status}
                            </span>
                            {hwStatus.motor?.port && ` (${hwStatus.motor.port})`}
                        </span>
                    </div>
                    <div className="hw-info-item">
                        <span className="hw-label">Camera</span>
                        <span className={`hw-value ${hwStatus.camera?.camera_active ? 'ok' : ''}`}>
                            {hwStatus.camera?.camera_active ? '✅' : '⏳'} MJPEG
                            {hwStatus.camera?.resolution && ` ${hwStatus.camera.resolution}`}
                            {hwStatus.camera?.source && ` (${hwStatus.camera.source})`}
                        </span>
                    </div>
                    <div className="hw-info-item">
                        <span className="hw-label">Platform</span>
                        <span className="hw-value">{hwStatus.platform || 'Unknown'}</span>
                    </div>
                    <div className="hw-info-item">
                        <span className="hw-label">Line-Follow</span>
                        <span className={`hw-value ${hwStatus.line_follower ? 'ok' : ''}`}>
                            {hwStatus.line_follower ? '✅ Canny active' : '⚠️ Fallback / off'}
                        </span>
                    </div>
                </div>
            )}

            {detected && (
                <div style={{ marginTop: '1rem', fontSize: '0.85rem' }}>
                    <strong>Detected Hardware:</strong>
                    <ul style={{ marginTop: '0.3rem', paddingLeft: '1.2rem' }}>
                        <li>GPIO: {detected.gpio ? '✅' : '❌'}</li>
                        <li>Camera: {detected.camera ? '✅' : '❌'} {detected.camera_info && `(${detected.camera_info.split('\n')[1]?.trim()})`}</li>
                        <li>Serial: {detected.serial ? `✅ ${detected.serial_ports?.join(', ')}` : '❌'}</li>
                        <li>SPI: {detected.spi ? `✅ ${detected.spi_devices?.join(', ')}` : '❌'}</li>
                        <li>I2C: {detected.i2c_buses?.length ? `✅ ${detected.i2c_buses.join(', ')}` : '❌'}</li>
                    </ul>
                </div>
            )}
        </div>
    );
}

export default HardwareStatus;
