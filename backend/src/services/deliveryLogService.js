const DeliveryLog = require('../entities/DeliveryLog');

class DeliveryLogService {
    async create(data) {
        const log = new DeliveryLog(data);
        return log.save();
    }

    async startDelivery(logId) {
        return DeliveryLog.findByIdAndUpdate(logId, {
            startAt: new Date(),
            status: 'in-progress'
        }, { new: true });
    }

    async completeDelivery(logId) {
        const log = await DeliveryLog.findById(logId);
        if (!log) return null;
        const endAt = new Date();
        const timeProcess = log.startAt ? Math.round((endAt - log.startAt) / 1000) : null;
        log.endAt = endAt;
        log.timeProcess = timeProcess;
        log.status = 'completed';
        return log.save();
    }

    async failDelivery(logId) {
        return DeliveryLog.findByIdAndUpdate(logId, {
            endAt: new Date(),
            status: 'failed'
        }, { new: true });
    }

    async getAll(filter = {}) {
        const query = {};
        if (filter.staffId) query.staff = filter.staffId;
        if (filter.status) query.status = filter.status;

        return DeliveryLog.find(query)
            .populate('customer', 'displayName username')
            .populate('staff', 'displayName username')
            .populate('order', 'totalPrice destinationPoint items status')
            .sort({ createdAt: -1 });
    }

    async getById(id) {
        return DeliveryLog.findById(id)
            .populate('customer', 'displayName username')
            .populate('staff', 'displayName username')
            .populate('order', 'totalPrice destinationPoint items status');
    }

    async getByOrderId(orderId) {
        return DeliveryLog.findOne({ order: orderId })
            .populate('customer', 'displayName username')
            .populate('staff', 'displayName username');
    }

    async getStats() {
        const total = await DeliveryLog.countDocuments();
        const completed = await DeliveryLog.countDocuments({ status: 'completed' });
        const failed = await DeliveryLog.countDocuments({ status: 'failed' });
        const inProgress = await DeliveryLog.countDocuments({ status: 'in-progress' });

        const avgTime = await DeliveryLog.aggregate([
            { $match: { status: 'completed', timeProcess: { $ne: null } } },
            { $group: { _id: null, avg: { $avg: '$timeProcess' } } }
        ]);

        return {
            total,
            completed,
            failed,
            inProgress,
            avgTimeProcess: avgTime.length > 0 ? Math.round(avgTime[0].avg) : 0
        };
    }
}

module.exports = new DeliveryLogService();
