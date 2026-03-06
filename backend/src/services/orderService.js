const Order = require('../entities/Order');
const DeliveryLog = require('../entities/DeliveryLog');

class OrderService {
    async createOrder(orderData) {
        const order = new Order(orderData);
        return await order.save();
    }

    async getAllOrders() {
        return await Order.find()
            .populate('customer', 'displayName username')
            .populate('confirmedBy', 'displayName username')
            .sort({ createdAt: -1 });
    }

    async getPendingOrders() {
        return await Order.find({ status: 'pending' })
            .populate('customer', 'displayName username')
            .sort({ createdAt: -1 });
    }

    async getOrdersByCustomer(customerId) {
        return await Order.find({ customer: customerId })
            .sort({ createdAt: -1 });
    }

    async getOrderById(id) {
        return await Order.findById(id)
            .populate('customer', 'displayName username')
            .populate('confirmedBy', 'displayName username');
    }

    async updateOrderStatus(id, status) {
        return await Order.findByIdAndUpdate(
            id,
            { status },
            { new: true }
        );
    }

    async confirmOrder(id, staffId) {
        const order = await Order.findByIdAndUpdate(
            id,
            { status: 'confirmed', vehicleStatus: 'moving', confirmedBy: staffId },
            { new: true }
        );
        if (order) {
            // Create delivery log
            await DeliveryLog.create({
                order: order._id,
                customer: order.customer,
                staff: staffId,
                destinationPoint: order.destinationPoint,
                status: 'pending'
            });
        }
        return order;
    }

    async startDelivery(id) {
        const order = await Order.findByIdAndUpdate(
            id,
            { status: 'delivering', vehicleStatus: 'moving' },
            { new: true }
        );
        if (order) {
            const log = await DeliveryLog.findOne({ order: order._id });
            if (log) {
                log.startAt = new Date();
                log.status = 'in-progress';
                await log.save();
            }
        }
        return order;
    }

    async markArrived(id) {
        const order = await Order.findByIdAndUpdate(
            id,
            { status: 'arrived', vehicleStatus: 'arrived' },
            { new: true }
        );
        return order;
    }

    async markDelivered(id) {
        const order = await Order.findByIdAndUpdate(
            id,
            { status: 'delivered', vehicleStatus: 'idle' },
            { new: true }
        );
        if (order) {
            const log = await DeliveryLog.findOne({ order: order._id });
            if (log) {
                log.endAt = new Date();
                log.timeProcess = log.startAt ? Math.round((log.endAt - log.startAt) / 1000) : null;
                log.status = 'completed';
                await log.save();
            }
        }
        return order;
    }

    async getDeliveringOrders() {
        return await Order.find({ status: { $in: ['delivering', 'confirmed'] } })
            .populate('customer', 'displayName username')
            .sort({ createdAt: -1 });
    }

    async cancelOrder(id) {
        const order = await Order.findByIdAndUpdate(
            id,
            { status: 'cancelled', vehicleStatus: 'idle' },
            { new: true }
        );
        if (order) {
            await DeliveryLog.findOneAndUpdate(
                { order: order._id },
                { status: 'failed', endAt: new Date() }
            );
        }
        return order;
    }
}

module.exports = new OrderService();
