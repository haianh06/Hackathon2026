const vehicleService = require('../services/vehicleService');
const hardwareService = require('../services/hardwareService');
const orderService = require('../services/orderService');
const notificationService = require('../services/notificationService');

function setupSocket(io) {
    io.on('connection', (socket) => {
        console.log(`Client connected: ${socket.id}`);

        // Client joins a room (staff, customer, or hardware)
        socket.on('join-room', (room) => {
            socket.join(room);
            console.log(`${socket.id} joined room: ${room}`);
        });

        // Customer joins their personal room for targeted notifications
        socket.on('join-customer', (customerId) => {
            socket.join(`customer-${customerId}`);
            console.log(`${socket.id} joined customer room: customer-${customerId}`);
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
        socket.on('navigation-complete', async (data) => {
            io.emit('navigation-complete', data);

            // Save vehicle heading for next navigation
            if (data.heading) {
                try {
                    await vehicleService.updateHeading(data.heading);
                } catch (err) {
                    console.error('Error saving vehicle heading:', err);
                }
            }

            // ── Return trip completed — reset vehicle to start ──
            if (data.isReturn) {
                try {
                    await vehicleService.returnToWarehouse();
                    io.emit('vehicle-returned', { message: 'Xe đã về điểm xuất phát' });
                    console.log('Vehicle returned to start');
                } catch (err) {
                    console.error('Error resetting vehicle after return:', err);
                }
                return;
            }

            // Check if this is a delivery completion (not a return trip)
            if (data.orderId && !data.isReturn) {
                try {
                    const order = await orderService.getOrderById(data.orderId);
                    if (order && (order.status === 'delivering' || order.status === 'confirmed')) {
                        // Mark order as arrived
                        const updatedOrder = await orderService.markArrived(data.orderId);

                        // Helper to notify a customer about order arrival
                        const notifyArrival = async (arrivedOrder, custId) => {
                            io.emit('order-arrived', {
                                order: arrivedOrder,
                                message: `Đơn hàng #${arrivedOrder._id.toString().slice(-6).toUpperCase()} đã được giao tới ${arrivedOrder.destinationPoint}. Vui lòng lấy hàng và xác nhận!`
                            });
                            if (custId) {
                                try {
                                    await notificationService.createAndEmit(io, {
                                        user: custId,
                                        order: arrivedOrder._id,
                                        type: 'order_arrived',
                                        title: 'Đơn hàng đã đến!',
                                        message: `Đơn hàng #${arrivedOrder._id.toString().slice(-6).toUpperCase()} đã được giao tới điểm ${arrivedOrder.destinationPoint}. Vui lòng lấy hàng và xác nhận đơn hàng.`
                                    });
                                } catch (notifErr) {
                                    console.error('Error creating notification:', notifErr);
                                }
                                io.to(`customer-${custId}`).emit('delivery-notification', {
                                    orderId: arrivedOrder._id,
                                    type: 'arrived',
                                    title: 'Đơn hàng đã đến!',
                                    message: `Đơn hàng #${arrivedOrder._id.toString().slice(-6).toUpperCase()} đã được giao tới điểm ${arrivedOrder.destinationPoint}. Vui lòng lấy hàng và xác nhận đơn hàng.`,
                                    destinationPoint: arrivedOrder.destinationPoint
                                });
                            }
                        };

                        // Notify for the primary order
                        const primaryCustomerId = order.customer ? (order.customer._id || order.customer) : null;
                        await notifyArrival(updatedOrder, primaryCustomerId);

                        // If batch order, also mark all sibling orders at the same destination as arrived
                        if (order.batchId) {
                            const siblings = await orderService.getBatchOrdersAtDestination(
                                order.batchId, order.destinationPoint
                            );
                            for (const sibling of siblings) {
                                if (sibling._id.toString() === data.orderId) continue;
                                await orderService.startDelivery(sibling._id);
                                const arrivedSibling = await orderService.markArrived(sibling._id);
                                const sibCustId = sibling.customer ? (sibling.customer._id || sibling.customer) : null;
                                await notifyArrival(arrivedSibling, sibCustId);
                            }
                        }
                    }
                } catch (err) {
                    console.error('Error handling navigation complete for order:', err);
                }
            }
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

        // ====== Map Builder Events ======
        // Frontend → Hardware: move one step forward with canny centering
        socket.on('map-build-step', (data) => {
            io.to('hardware').emit('map-build-step', data || {});
        });

        // Frontend → Hardware: turn at intersection
        socket.on('map-build-turn', (data) => {
            io.to('hardware').emit('map-build-turn', data);
        });

        // Frontend → Hardware: stop map build movement
        socket.on('map-build-stop', () => {
            io.to('hardware').emit('map-build-stop');
        });

        // Frontend → Hardware: request current canny analysis
        socket.on('map-build-analyse', () => {
            io.to('hardware').emit('map-build-analyse');
        });

        // Hardware → Frontend: coordinate update from map builder
        socket.on('map-build-position', (data) => {
            io.to('admin').emit('map-build-position', data);
        });

        // Hardware → Frontend: canny analysis result
        socket.on('map-build-analysis', (data) => {
            io.to('admin').emit('map-build-analysis', data);
        });

        // Hardware → Frontend: map build status
        socket.on('map-build-status', (data) => {
            io.to('admin').emit('map-build-status', data);
        });

        // ====== RFID Events ======
        // Frontend → Hardware: start RFID scan
        socket.on('rfid-start-scan', () => {
            io.to('hardware').emit('rfid-start-scan');
        });

        // Frontend → Hardware: stop RFID scan
        socket.on('rfid-stop-scan', () => {
            io.to('hardware').emit('rfid-stop-scan');
        });

        // Hardware → Frontend: RFID tag scanned
        socket.on('rfid-scanned', (data) => {
            io.to('admin').emit('rfid-scanned', data);
        });

        // Hardware → Frontend: RFID scan status (scanning/stopped)
        socket.on('rfid-scan-status', (data) => {
            io.to('admin').emit('rfid-scan-status', data);
        });

        // ====== Motor Calibration Events ======
        // Frontend → Hardware: send raw PWM pulse to a specific motor
        socket.on('motor-calibrate-set', (data) => {
            io.to('hardware').emit('motor-calibrate-set', data);
        });

        // Frontend → Hardware: run a sweep test (ramp PWM up/down)
        socket.on('motor-calibrate-sweep', (data) => {
            io.to('hardware').emit('motor-calibrate-sweep', data);
        });

        // Frontend → Hardware: run a step response test
        socket.on('motor-calibrate-step', (data) => {
            io.to('hardware').emit('motor-calibrate-step', data);
        });

        // Frontend → Hardware: stop calibration test
        socket.on('motor-calibrate-stop', () => {
            io.to('hardware').emit('motor-calibrate-stop');
        });

        // Hardware → Frontend: calibration data point (real-time)
        socket.on('motor-calibrate-data', (data) => {
            io.to('admin').emit('motor-calibrate-data', data);
        });

        // Frontend → Hardware: deadband test
        socket.on('motor-calibrate-deadband', (data) => {
            io.to('hardware').emit('motor-calibrate-deadband', data);
        });

        // ====== Road Sign Detection Events ======
        // Frontend → Hardware: start continuous sign detection
        socket.on('sign-detect-start', () => {
            io.to('hardware').emit('sign-detect-start');
        });

        // Frontend → Hardware: stop sign detection
        socket.on('sign-detect-stop', () => {
            io.to('hardware').emit('sign-detect-stop');
        });

        // Frontend → Hardware: single-frame detection
        socket.on('sign-detect-once', () => {
            io.to('hardware').emit('sign-detect-once');
        });

        // Hardware → Frontend: continuous detection results
        socket.on('sign-detected', (data) => {
            io.emit('sign-detected', data);
        });

        // Hardware → Frontend: detection status (running/stopped)
        socket.on('sign-detect-status', (data) => {
            io.emit('sign-detect-status', data);
        });

        // Hardware → Frontend: single detection result
        socket.on('sign-detect-result', (data) => {
            io.emit('sign-detect-result', data);
        });

        socket.on('disconnect', () => {
            console.log(`Client disconnected: ${socket.id}`);
        });
    });
}

module.exports = setupSocket;
