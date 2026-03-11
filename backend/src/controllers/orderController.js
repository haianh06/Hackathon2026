const orderService = require('../services/orderService');
const mapService = require('../services/mapService');
const vehicleService = require('../services/vehicleService');
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

                    // Include vehicle heading so daemon knows current facing direction
                    const vehicle = await vehicleService.getVehicle();
                    io.to('hardware').emit('auto-navigate', {
                        path: pathData,
                        returnPath: [],
                        orderId: order._id.toString(),
                        heading: vehicle.heading || null
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

                // Check if there are more orders in this batch
                const nextOrder = order.batchId
                    ? await orderService.getNextBatchOrder(order.batchId, order.batchOrder)
                    : null;

                if (nextOrder) {
                    // Navigate to the next order's destination
                    await orderService.startDelivery(nextOrder._id);
                    const nextPath = await mapService.findPath(order.destinationPoint, nextOrder.destinationPoint);
                    if (nextPath && nextPath.length >= 2) {
                        const pathData = nextPath.map(p => ({ pointId: p.pointId, x: p.x, y: p.y }));
                        const vehicle = await vehicleService.getVehicle();
                        io.to('hardware').emit('auto-navigate', {
                            path: pathData,
                            returnPath: [],
                            orderId: nextOrder._id.toString(),
                            batchId: order.batchId,
                            heading: vehicle.heading || null
                        });
                        io.emit('vehicle-dispatch', { path: nextPath, orderId: nextOrder._id.toString() });
                    }

                    // Notify customer of next order
                    if (nextOrder.customer) {
                        try {
                            const nextCustomerId = nextOrder.customer._id || nextOrder.customer;
                            await notificationService.createAndEmit(io, {
                                user: nextCustomerId,
                                order: nextOrder._id,
                                type: 'order_delivering',
                                title: 'Xe đang trên đường giao!',
                                message: `Đơn hàng #${nextOrder._id.toString().slice(-6).toUpperCase()} đang được giao đến điểm ${nextOrder.destinationPoint}.`
                            });
                        } catch (e) { console.error(e); }
                    }
                } else {
                    // No more orders — return the vehicle to start
                    const returnPath = await mapService.findPath(order.destinationPoint, 'S');
                    if (returnPath && returnPath.length >= 2) {
                        const returnData = returnPath.map(p => ({ pointId: p.pointId, x: p.x, y: p.y }));
                        const vehicle = await vehicleService.getVehicle();
                        io.to('hardware').emit('auto-navigate', {
                            path: returnData,
                            returnPath: [],
                            orderId: order._id.toString(),
                            isReturn: true,
                            heading: vehicle.heading || null
                        });
                        io.emit('vehicle-returning', { orderId: order._id.toString(), path: returnPath });
                    }
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

    // Batch confirm multiple orders — vehicle delivers sequentially
    async batchConfirm(req, res) {
        try {
            const { orderIds } = req.body;
            if (!orderIds || orderIds.length === 0) {
                return res.status(400).json({ success: false, message: 'Chọn ít nhất 1 đơn hàng' });
            }

            const staffId = req.user ? req.user.id : null;
            const batchId = `batch-${Date.now()}`;
            const io = req.app.get('io');

            // Build sequential route: S → dest1 → dest2 → ...
            const confirmedOrders = [];
            const fullPath = [];
            let currentPoint = 'S';

            for (let i = 0; i < orderIds.length; i++) {
                const order = await orderService.confirmOrder(orderIds[i], staffId);
                if (!order) continue;

                // Assign batch info
                order.batchId = batchId;
                order.batchOrder = i;
                await order.save();

                const segment = await mapService.findPath(currentPoint, order.destinationPoint);
                confirmedOrders.push(order);

                if (segment && segment.length >= 2) {
                    // Skip first point of subsequent segments (it's the same as last of previous)
                    const pts = i === 0 ? segment : segment.slice(1);
                    fullPath.push(...pts);
                    currentPoint = order.destinationPoint;
                }

                // Notify customer
                if (io && order.customer) {
                    try {
                        await notificationService.createAndEmit(io, {
                            user: order.customer,
                            order: order._id,
                            type: 'order_confirmed',
                            title: 'Đơn hàng đã được xác nhận!',
                            message: `Đơn hàng #${order._id.toString().slice(-6).toUpperCase()} đã được xác nhận. Xe đang trên đường giao.`
                        });
                    } catch (e) { console.error(e); }
                }
            }

            if (io && confirmedOrders.length > 0) {
                io.emit('order-confirmed', { orders: confirmedOrders, batchId });

                // Start delivery for the first order
                if (confirmedOrders[0]) {
                    await orderService.startDelivery(confirmedOrders[0]._id);
                }

                // Navigate to the FIRST destination only
                const firstDest = confirmedOrders[0].destinationPoint;
                const firstPath = await mapService.findPath('S', firstDest);
                if (firstPath && firstPath.length >= 2) {
                    const pathData = firstPath.map(p => ({ pointId: p.pointId, x: p.x, y: p.y }));
                    const vehicle = await vehicleService.getVehicle();
                    io.to('hardware').emit('auto-navigate', {
                        path: pathData,
                        returnPath: [],
                        orderId: confirmedOrders[0]._id.toString(),
                        batchId,
                        heading: vehicle.heading || null
                    });
                }
            }

            res.json({
                success: true,
                data: {
                    batchId,
                    orders: confirmedOrders,
                    fullPath,
                    totalOrders: confirmedOrders.length
                }
            });
        } catch (error) {
            res.status(500).json({ success: false, message: error.message });
        }
    }
}

module.exports = new OrderController();
