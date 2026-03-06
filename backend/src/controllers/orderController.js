const orderService = require('../services/orderService');
const mapService = require('../services/mapService');
const notificationService = require('../services/notificationService');

class OrderController {
    async create(req, res) {
        try {
            const { items, destinationPoint, note } = req.body;

            if (!items || items.length === 0) {
                return res.status(400).json({ success: false, message: 'Vui lòng chọn ít nhất 1 sản phẩm' });
            }

            if (!destinationPoint) {
                return res.status(400).json({ success: false, message: 'Vui lòng chọn vị trí nhận hàng' });
            }

            const totalPrice = items.reduce((sum, item) => sum + (item.price * item.quantity), 0);

            const order = await orderService.createOrder({
                items,
                totalPrice,
                customer: req.user ? req.user.id : null,
                customerName: req.user ? req.user.username : 'Khách hàng',
                destinationPoint,
                note: note || ''
            });

            const io = req.app.get('io');
            if (io) {
                io.emit('new-order', order);
            }

            res.status(201).json({ success: true, data: order });
        } catch (error) {
            res.status(500).json({ success: false, message: error.message });
        }
    }

    async getAll(req, res) {
        try {
            const orders = await orderService.getAllOrders();
            res.json({ success: true, data: orders });
        } catch (error) {
            res.status(500).json({ success: false, message: error.message });
        }
    }

    async getMyOrders(req, res) {
        try {
            const orders = await orderService.getOrdersByCustomer(req.user.id);
            res.json({ success: true, data: orders });
        } catch (error) {
            res.status(500).json({ success: false, message: error.message });
        }
    }

    async getPending(req, res) {
        try {
            const orders = await orderService.getPendingOrders();
            res.json({ success: true, data: orders });
        } catch (error) {
            res.status(500).json({ success: false, message: error.message });
        }
    }

    async getById(req, res) {
        try {
            const order = await orderService.getOrderById(req.params.id);
            if (!order) {
                return res.status(404).json({ success: false, message: 'Không tìm thấy đơn hàng' });
            }
            res.json({ success: true, data: order });
        } catch (error) {
            res.status(500).json({ success: false, message: error.message });
        }
    }

    async confirm(req, res) {
        try {
            const staffId = req.user ? req.user.id : null;
            const order = await orderService.confirmOrder(req.params.id, staffId);
            if (!order) {
                return res.status(404).json({ success: false, message: 'Không tìm thấy đơn hàng' });
            }

            const path = await mapService.findPath('S', order.destinationPoint);

            const io = req.app.get('io');
            if (io) {
                io.emit('order-confirmed', { order, path });

                // Notify customer that order is confirmed
                if (order.customer) {
                    try {
                        await notificationService.createAndEmit(io, {
                            user: order.customer,
                            order: order._id,
                            type: 'order_confirmed',
                            title: 'Đơn hàng đã được xác nhận!',
                            message: `Đơn hàng #${order._id.toString().slice(-6).toUpperCase()} đã được xác nhận. Xe đang trên đường giao đến điểm ${order.destinationPoint}.`
                        });
                    } catch (notifErr) {
                        console.error('Error creating confirm notification:', notifErr);
                    }
                }

                if (path && path.length >= 2) {
                    const pathData = path.map(p => ({ pointId: p.pointId, x: p.x, y: p.y }));

                    await orderService.startDelivery(req.params.id);

                    // Only send delivery path, no return path
                    // Vehicle will return after customer confirms
                    io.to('hardware').emit('auto-navigate', {
                        path: pathData,
                        returnPath: [],
                        orderId: order._id.toString()
                    });
                }
            }

            res.json({ success: true, data: { order, path } });
        } catch (error) {
            res.status(500).json({ success: false, message: error.message });
        }
    }

    async markDelivered(req, res) {
        try {
            const order = await orderService.markDelivered(req.params.id);
            if (!order) {
                return res.status(404).json({ success: false, message: 'Không tìm thấy đơn hàng' });
            }

            const io = req.app.get('io');
            if (io) {
                io.emit('order-delivered', order);
            }

            res.json({ success: true, data: order });
        } catch (error) {
            res.status(500).json({ success: false, message: error.message });
        }
    }

    // Customer confirms they received the order -> vehicle returns to start
    async customerConfirm(req, res) {
        try {
            const order = await orderService.getOrderById(req.params.id);
            if (!order) {
                return res.status(404).json({ success: false, message: 'Không tìm thấy đơn hàng' });
            }

            if (order.status !== 'arrived') {
                return res.status(400).json({ success: false, message: 'Đơn hàng chưa đến nơi' });
            }

            // Mark as delivered
            const updatedOrder = await orderService.markDelivered(req.params.id);

            const io = req.app.get('io');
            if (io) {
                io.emit('order-delivered', updatedOrder);

                // Save completion notification for customer
                if (order.customer) {
                    try {
                        const customerId = order.customer._id || order.customer;
                        await notificationService.createAndEmit(io, {
                            user: customerId,
                            order: updatedOrder._id,
                            type: 'order_delivered',
                            title: 'Giao hàng hoàn tất!',
                            message: `Đơn hàng #${updatedOrder._id.toString().slice(-6).toUpperCase()} đã được giao thành công. Cảm ơn bạn!`
                        });
                    } catch (notifErr) {
                        console.error('Error creating delivery notification:', notifErr);
                    }
                }

                // Auto-navigate vehicle back to start
                const returnPath = await mapService.findPath(order.destinationPoint, 'S');
                if (returnPath && returnPath.length >= 2) {
                    const returnData = returnPath.map(p => ({ pointId: p.pointId, x: p.x, y: p.y }));
                    io.to('hardware').emit('auto-navigate', {
                        path: returnData,
                        returnPath: [],
                        orderId: order._id.toString(),
                        isReturn: true
                    });
                    io.emit('vehicle-returning', { orderId: order._id.toString(), path: returnPath });
                }
            }

            res.json({ success: true, data: updatedOrder });
        } catch (error) {
            res.status(500).json({ success: false, message: error.message });
        }
    }

    async cancel(req, res) {
        try {
            const order = await orderService.cancelOrder(req.params.id);
            if (!order) {
                return res.status(404).json({ success: false, message: 'Không tìm thấy đơn hàng' });
            }

            const io = req.app.get('io');
            if (io) {
                io.emit('order-cancelled', order);
            }

            res.json({ success: true, data: order });
        } catch (error) {
            res.status(500).json({ success: false, message: error.message });
        }
    }
}

module.exports = new OrderController();
