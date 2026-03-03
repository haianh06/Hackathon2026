const express = require('express');
const router = express.Router();
const deliveryLogService = require('../services/deliveryLogService');
const { authenticate, authorize } = require('../middleware/auth');

// GET /api/delivery-logs — admin only
router.get('/', authenticate, authorize('admin', 'staff'), async (req, res) => {
    try {
        const filter = {};
        if (req.query.staffId) filter.staffId = req.query.staffId;
        if (req.query.status) filter.status = req.query.status;
        const logs = await deliveryLogService.getAll(filter);
        res.json({ success: true, data: logs });
    } catch (err) {
        res.status(500).json({ success: false, message: err.message });
    }
});

// GET /api/delivery-logs/stats — admin only
router.get('/stats', authenticate, authorize('admin'), async (req, res) => {
    try {
        const stats = await deliveryLogService.getStats();
        res.json({ success: true, data: stats });
    } catch (err) {
        res.status(500).json({ success: false, message: err.message });
    }
});

// GET /api/delivery-logs/:id
router.get('/:id', authenticate, async (req, res) => {
    try {
        const log = await deliveryLogService.getById(req.params.id);
        if (!log) return res.status(404).json({ success: false, message: 'Log not found' });
        res.json({ success: true, data: log });
    } catch (err) {
        res.status(500).json({ success: false, message: err.message });
    }
});

module.exports = router;
