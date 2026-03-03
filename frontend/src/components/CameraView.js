import React, { useState } from 'react';

function CameraView({ autoConnect = false }) {
    const [status, setStatus] = useState(autoConnect ? 'connected' : 'disconnected');
    const [error, setError] = useState(null);

    // MJPEG stream URL — served by hardware daemon via nginx proxy
    const streamUrl = '/camera/stream';
    const snapshotUrl = '/camera/snapshot';

    const connectCamera = () => {
        setStatus('connected');
        setError(null);
    };

    const disconnectCamera = () => {
        setStatus('disconnected');
    };

    const handleImgError = () => {
        setError('Camera không phản hồi');
        setStatus('error');
    };

    return (
        <div className="camera-view">
            <div className="camera-header">
                <h4>📷 Camera xe (MJPEG)</h4>
                <div className="camera-controls">
                    {status === 'disconnected' && (
                        <button className="btn btn-primary btn-sm" onClick={connectCamera}>
                            ▶ Kết nối Camera
                        </button>
                    )}
                    {status === 'connected' && (
                        <button className="btn btn-danger btn-sm" onClick={disconnectCamera}>
                            ⏹ Ngắt Camera
                        </button>
                    )}
                    {status === 'error' && (
                        <button className="btn btn-primary btn-sm" onClick={connectCamera}>
                            🔄 Thử lại
                        </button>
                    )}
                    <span className={`camera-status-dot ${status}`}></span>
                </div>
            </div>

            <div className="camera-video-wrapper">
                {status === 'connected' ? (
                    <img
                        src={streamUrl}
                        alt="Camera Feed"
                        className="camera-video"
                        onError={handleImgError}
                        style={{ width: '100%', height: 'auto', background: '#000' }}
                    />
                ) : (
                    <div className="camera-placeholder">
                        {status === 'disconnected' && '📷 Nhấn "Kết nối Camera" để xem'}
                        {status === 'error' && `❌ Lỗi: ${error || 'Không kết nối được'}`}
                    </div>
                )}
            </div>
        </div>
    );
}

export default CameraView;
