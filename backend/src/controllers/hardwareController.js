const hardwareService = require('../services/hardwareService');

class HardwareController {
    async getStatus(req, res) {
        try {
            const status = hardwareService.getStatus();
            res.json({ success: true, data: status });
        } catch (error) {
            res.status(500).json({ success: false, message: error.message });
        }
    }

    async detectHardware(req, res) {
        try {
            const hardware = await hardwareService.detectHardware();
            res.json({ success: true, data: hardware });
        } catch (error) {
            res.status(500).json({ success: false, message: error.message });
        }
    }

    async sendMotorCommand(req, res) {
        try {
            const { command, speed } = req.body;
            const validCommands = ['forward', 'backward', 'left', 'right', 'stop'];

            if (!validCommands.includes(command)) {
                return res.status(400).json({
                    success: false,
                    message: `Lệnh không hợp lệ. Chấp nhận: ${validCommands.join(', ')}`
                });
            }

            const io = req.app.get('io');
            hardwareService.sendMotorCommand(io, command, speed || 50);

            res.json({
                success: true,
                data: { command, speed: speed || 50, sent: true }
            });
        } catch (error) {
            res.status(500).json({ success: false, message: error.message });
        }
    }

    async navigate(req, res) {
        try {
            const { path } = req.body;
            if (!path || path.length === 0) {
                return res.status(400).json({ success: false, message: 'Cần cung cấp đường đi' });
            }

            const io = req.app.get('io');
            hardwareService.sendNavigationCommand(io, path);

            res.json({ success: true, data: { navigating: true, path } });
        } catch (error) {
            res.status(500).json({ success: false, message: error.message });
        }
    }

    async startDaemon(req, res) {
        try {
            hardwareService.startDaemon();
            res.json({ success: true, message: 'Hardware daemon started' });
        } catch (error) {
            res.status(500).json({ success: false, message: error.message });
        }
    }

    async stopDaemon(req, res) {
        try {
            hardwareService.stopDaemon();
            res.json({ success: true, message: 'Hardware daemon stopped' });
        } catch (error) {
            res.status(500).json({ success: false, message: error.message });
        }
    }

    // ====== Road Sign Detection (C++ container) ======

    /**
     * Receive continuous detection results from C++ roadsign container.
     * POSTed by C++ detection loop → broadcast via Socket.IO to frontend.
     */
    async receiveSignDetections(req, res) {
        try {
            const { detections, count, timestamp, inference_ms } = req.body;
            const io = req.app.get('io');
            io.emit('sign-detected', { detections, count, timestamp, inference_ms });
            res.json({ success: true });
        } catch (error) {
            res.status(500).json({ success: false, message: error.message });
        }
    }

    /**
     * Receive single-frame detection result from C++ container.
     */
    async receiveSignDetectResult(req, res) {
        try {
            const { detections, count, timestamp } = req.body;
            const io = req.app.get('io');
            io.emit('sign-detect-result', { detections, count, timestamp });
            res.json({ success: true });
        } catch (error) {
            res.status(500).json({ success: false, message: error.message });
        }
    }

    /**
     * Forward start/stop/detect_once commands to C++ roadsign container.
     */
    async signDetectControl(req, res) {
        try {
            const { action } = req.params;
            const validActions = ['start', 'stop', 'detect_once', 'health'];
            if (!validActions.includes(action)) {
                return res.status(400).json({ success: false, message: 'Invalid action' });
            }

            const roadsignUrl = process.env.ROADSIGN_URL || 'http://roadsign:9001';
            const method = action === 'health' ? 'GET' : 'POST';
            const url = `${roadsignUrl}/${action}`;

            const response = await fetch(url, { method, signal: AbortSignal.timeout(5000) });
            const data = await response.json();

            // Broadcast status change to frontend
            const io = req.app.get('io');
            if (action === 'start' || action === 'stop') {
                io.emit('sign-detect-status', { detecting: action === 'start' });
            }

            res.json({ success: true, data });
        } catch (error) {
            res.status(502).json({ success: false, message: `C++ detector: ${error.message}` });
        }
    }
}

module.exports = new HardwareController();
