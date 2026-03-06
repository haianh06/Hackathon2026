const express = require('express');
const router = express.Router();
const notificationService = require('../services/notificationService');
const { authenticate } = require('../middleware/auth');

// GET /api/notifications — get current user's notifications
router.get('/', authenticate, async (req, res) => {
    try {
        const limit = parseInt(req.query.limit) || 20;
        const skip = parseInt(req.query.skip) || 0;
        const unreadOnly = req.query.unread === 'true';
        const notifications = await notificationService.getByUser(req.user.id, { limit, skip, unreadOnly });
        res.json({ success: true, data: notifications });
    } catch (err) {
        res.status(500).json({ success: false, message: err.message });
    }
});

// GET /api/notifications/unread-count
router.get('/unread-count', authenticate, async (req, res) => {
    try {
        const count = await notificationService.getUnreadCount(req.user.id);
        res.json({ success: true, data: { count } });
    } catch (err) {
        res.status(500).json({ success: false, message: err.message });
    }
});

// PUT /api/notifications/:id/read — mark one as read
router.put('/:id/read', authenticate, async (req, res) => {
    try {
        const notification = await notificationService.markAsRead(req.params.id, req.user.id);
        if (!notification) {
            return res.status(404).json({ success: false, message: 'Notification not found' });
        }
        res.json({ success: true, data: notification });
    } catch (err) {
        res.status(500).json({ success: false, message: err.message });
    }
});

// PUT /api/notifications/read-all — mark all as read
router.put('/read-all', authenticate, async (req, res) => {
    try {
        await notificationService.markAllAsRead(req.user.id);
        res.json({ success: true, message: 'All notifications marked as read' });
    } catch (err) {
        res.status(500).json({ success: false, message: err.message });
    }
});

module.exports = router;
