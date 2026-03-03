const orderService = require('../services/orderService');
const mapService = require('../services/mapService');

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

                if (path && path.length >= 2) {
                    const returnPath = await mapService.findPath(order.destinationPoint, 'S');
                    const pathData = path.map(p => ({ pointId: p.pointId, x: p.x, y: p.y }));
                    const returnData = returnPath ? returnPath.map(p => ({ pointId: p.pointId, x: p.x, y: p.y })) : [];

                    await orderService.startDelivery(req.params.id);

                    io.to('hardware').emit('auto-navigate', {
                        path: pathData,
                        returnPath: returnData,
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
