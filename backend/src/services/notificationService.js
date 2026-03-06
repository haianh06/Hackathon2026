const Notification = require('../entities/Notification');

class NotificationService {
    async create(data) {
        const notification = new Notification(data);
        return notification.save();
    }

    async getByUser(userId, { limit = 20, skip = 0, unreadOnly = false } = {}) {
        const query = { user: userId };
        if (unreadOnly) query.read = false;
        return Notification.find(query)
            .populate('order', 'totalPrice destinationPoint status')
            .sort({ createdAt: -1 })
            .skip(skip)
            .limit(limit);
    }

    async getUnreadCount(userId) {
        return Notification.countDocuments({ user: userId, read: false });
    }

    async markAsRead(notificationId, userId) {
        return Notification.findOneAndUpdate(
            { _id: notificationId, user: userId },
            { read: true },
            { new: true }
        );
    }

    async markAllAsRead(userId) {
        return Notification.updateMany(
            { user: userId, read: false },
            { read: true }
        );
    }

    /**
     * Create a notification and emit it via Socket.IO
     */
    async createAndEmit(io, data) {
        const notification = await this.create(data);
        const populated = await Notification.findById(notification._id)
            .populate('order', 'totalPrice destinationPoint status');

        // Emit to the specific user's room
        io.to(`customer-${data.user}`).emit('new-notification', populated);
        return populated;
    }
}

module.exports = new NotificationService();
