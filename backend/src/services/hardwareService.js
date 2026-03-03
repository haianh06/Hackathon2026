const { spawn } = require('child_process');
const path = require('path');
const EventEmitter = require('events');

class HardwareService extends EventEmitter {
    constructor() {
        super();
        this.daemonProcess = null;
        this.isRunning = false;
        this.hardwareStatus = {
            motor: { driver: 'serial', type: 'dual_servo', status: 'idle' },
            camera: { streaming: 'mjpeg', camera_active: false },
            platform: 'Raspberry Pi 5',
            connected: false
        };
    }

    /**
     * Start the Python hardware daemon as a child process
     */
    startDaemon() {
        if (this.daemonProcess) {
            console.log('Hardware daemon already running');
            return;
        }

        const daemonPath = path.join(__dirname, '../../hardware/daemon.py');
        const serverUrl = `http://localhost:${process.env.PORT || 5000}`;
        const venvPython = path.join(__dirname, '../../hardware/venv/bin/python3');
        const fs = require('fs');
        const pythonBin = fs.existsSync(venvPython) ? venvPython : 'python3';

        console.log(`🔧 Starting hardware daemon: ${daemonPath} (python: ${pythonBin})`);

        this.daemonProcess = spawn(pythonBin, [daemonPath], {
            cwd: path.join(__dirname, '../../hardware'),
            env: {
                ...process.env,
                SERVER_URL: serverUrl,
                PYTHONUNBUFFERED: '1'
            },
            stdio: ['pipe', 'pipe', 'pipe']
        });

        this.daemonProcess.stdout.on('data', (data) => {
            const msg = data.toString().trim();
            if (msg) console.log(`[HW] ${msg}`);
        });

        this.daemonProcess.stderr.on('data', (data) => {
            const msg = data.toString().trim();
            if (msg) console.error(`[HW-ERR] ${msg}`);
        });

        this.daemonProcess.on('close', (code) => {
            console.log(`Hardware daemon exited with code ${code}`);
            this.daemonProcess = null;
            this.isRunning = false;
            this.hardwareStatus.connected = false;

            // Auto-restart after 5 seconds
            if (code !== 0) {
                console.log('Restarting hardware daemon in 5s...');
                setTimeout(() => this.startDaemon(), 5000);
            }
        });

        this.daemonProcess.on('error', (err) => {
            console.error(`Hardware daemon error: ${err.message}`);
            this.daemonProcess = null;
        });

        this.isRunning = true;
    }

    /**
     * Stop the Python hardware daemon
     */
    stopDaemon() {
        if (this.daemonProcess) {
            this.daemonProcess.kill('SIGTERM');
            this.daemonProcess = null;
            this.isRunning = false;
            console.log('Hardware daemon stopped');
        }
    }

    /**
     * Update hardware status (called when Python daemon reports status via Socket.IO)
     */
    updateStatus(status) {
        this.hardwareStatus = { ...status, connected: true };
        this.emit('status-update', this.hardwareStatus);
    }

    /**
     * Get current hardware status
     */
    getStatus() {
        return {
            ...this.hardwareStatus,
            daemon_running: this.isRunning,
            daemon_pid: this.daemonProcess?.pid || null
        };
    }

    /**
     * Send motor command via Socket.IO (io instance passed from server)
     */
    sendMotorCommand(io, command, speed = 50) {
        io.to('hardware').emit('motor-command', { command, speed });
    }

    /**
     * Send navigation command via Socket.IO
     */
    sendNavigationCommand(io, path) {
        io.to('hardware').emit('navigate-to', { path });
    }

    /**
     * Detect connected hardware (GPIO, I2C, camera, etc.)
     */
    async detectHardware() {
        const { execSync } = require('child_process');
        const result = {
            gpio: false,
            i2c_devices: [],
            camera: false,
            serial: false,
            spi: false
        };

        try {
            // Check GPIO (Pi 5 uses gpiochip4 for user GPIO)
            const gpioCheck = execSync('ls /dev/gpiochip0 /dev/gpiochip4 2>/dev/null', { encoding: 'utf-8' });
            result.gpio = gpioCheck.trim().length > 0;
        } catch (e) { /* not available */ }

        try {
            // Check I2C devices
            const i2cBuses = execSync('ls /dev/i2c-* 2>/dev/null', { encoding: 'utf-8' });
            result.i2c_buses = i2cBuses.trim().split('\n').filter(Boolean);

            // Scan I2C bus 4 (if available)
            try {
                const i2cScan = execSync('sudo i2cdetect -y 4 2>/dev/null | grep -oE "[0-9a-f]{2}" | head -20', { encoding: 'utf-8' });
                result.i2c_devices = i2cScan.trim().split('\n').filter(Boolean);
            } catch (e) { /* scan failed */ }
        } catch (e) { /* not available */ }

        try {
            // Check camera - try multiple detection methods
            let camDetected = false;
            let camInfo = '';
            try {
                const camCheck = execSync('rpicam-hello --list-cameras 2>&1 | head -5', { encoding: 'utf-8' });
                camDetected = camCheck.includes('imx') || camCheck.includes('Available cameras');
                camInfo = camCheck.trim();
            } catch (e2) {
                // Fallback: check /dev/video* devices
                try {
                    const videoDevs = execSync('ls /dev/video* 2>/dev/null', { encoding: 'utf-8' });
                    camDetected = videoDevs.trim().length > 0;
                    camInfo = `Video devices: ${videoDevs.trim()}`;
                } catch (e3) { /* no camera */ }
            }
            result.camera = camDetected;
            result.camera_info = camInfo;
        } catch (e) { /* not available */ }

        try {
            // Check serial
            const serialCheck = execSync('ls /dev/serial* /dev/ttyAMA* 2>/dev/null', { encoding: 'utf-8' });
            result.serial = serialCheck.trim().length > 0;
            result.serial_ports = serialCheck.trim().split('\n').filter(Boolean);
        } catch (e) { /* not available */ }

        try {
            // Check SPI
            const spiCheck = execSync('ls /dev/spidev* 2>/dev/null', { encoding: 'utf-8' });
            result.spi = spiCheck.trim().length > 0;
            result.spi_devices = spiCheck.trim().split('\n').filter(Boolean);
        } catch (e) { /* not available */ }

        return result;
    }
}

module.exports = new HardwareService();
