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
}

module.exports = new HardwareController();
