const vehicleService = require('../services/vehicleService');
const hardwareService = require('../services/hardwareService');

function setupSocket(io) {
    io.on('connection', (socket) => {
        console.log(`Client connected: ${socket.id}`);

        // Client joins a room (staff, customer, or hardware)
        socket.on('join-room', (room) => {
            socket.join(room);
            console.log(`${socket.id} joined room: ${room}`);
        });

        // Vehicle position update (from Pi hardware daemon)
        socket.on('vehicle-position-update', async (data) => {
            try {
                const { pointId } = data;
                await vehicleService.updatePosition(pointId);
                io.emit('vehicle-position', { pointId });
            } catch (error) {
                console.error('Vehicle position update error:', error);
            }
        });

        // ====== Hardware Daemon Events ======

        // Hardware status report from Python daemon
        socket.on('hardware-status', (status) => {
            hardwareService.updateStatus(status);
            io.to('staff').emit('hardware-status-update', status);
        });

        // Motor status from Python daemon
        socket.on('motor-status', (status) => {
            io.to('staff').emit('motor-status-update', status);
        });

        // Navigation complete from Python daemon
        socket.on('navigation-complete', (data) => {
            io.emit('navigation-complete', data);
        });

        // ====== Navigation real-time logs from Python daemon ======
        socket.on('navigation-log', (data) => {
            // Forward to all connected clients (staff + map page)
            io.emit('navigation-log', data);
        });

        // ====== Motor Control from Frontend ======
        socket.on('motor-control', (data) => {
            io.to('hardware').emit('motor-command', data);
        });

        // ====== Auto-Navigate from Frontend ======
        socket.on('auto-navigate', (data) => {
            io.to('hardware').emit('auto-navigate', data);
        });

        // ====== Stop Navigation from Frontend ======
        socket.on('stop-navigation', () => {
            io.to('hardware').emit('stop-navigation');
        });

        socket.on('disconnect', () => {
            console.log(`Client disconnected: ${socket.id}`);
        });
    });
}

module.exports = setupSocket;
