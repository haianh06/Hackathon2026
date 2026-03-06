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

            // Check if this is a delivery completion (not a return trip)
            if (data.orderId && !data.isReturn) {
                try {
                    const order = await orderService.getOrderById(data.orderId);
                    if (order && (order.status === 'delivering' || order.status === 'confirmed')) {
                        // Mark order as arrived
                        const updatedOrder = await orderService.markArrived(data.orderId);

                        // Notify everyone about arrival
                        io.emit('order-arrived', {
                            order: updatedOrder,
                            message: `Đơn hàng #${updatedOrder._id.toString().slice(-6).toUpperCase()} đã được giao tới ${updatedOrder.destinationPoint}. Vui lòng lấy hàng và xác nhận!`
                        });

                        // Targeted notification to the customer
                        if (order.customer) {
                            const customerId = order.customer._id || order.customer;

                            // Save notification to DB
                            try {
                                await notificationService.createAndEmit(io, {
                                    user: customerId,
                                    order: updatedOrder._id,
                                    type: 'order_arrived',
                                    title: 'Đơn hàng đã đến!',
                                    message: `Đơn hàng #${updatedOrder._id.toString().slice(-6).toUpperCase()} đã được giao tới điểm ${updatedOrder.destinationPoint}. Vui lòng lấy hàng và xác nhận đơn hàng.`
                                });
                            } catch (notifErr) {
                                console.error('Error creating notification:', notifErr);
                            }

                            io.to(`customer-${customerId}`).emit('delivery-notification', {
                                orderId: updatedOrder._id,
                                type: 'arrived',
                                title: 'Đơn hàng đã đến!',
                                message: `Đơn hàng #${updatedOrder._id.toString().slice(-6).toUpperCase()} đã được giao tới điểm ${updatedOrder.destinationPoint}. Vui lòng lấy hàng và xác nhận đơn hàng.`,
                                destinationPoint: updatedOrder.destinationPoint
                            });
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

        socket.on('disconnect', () => {
            console.log(`Client disconnected: ${socket.id}`);
        });
    });
}

module.exports = setupSocket;
