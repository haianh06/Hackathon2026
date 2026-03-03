const express = require('express');
const router = express.Router();
const authService = require('../services/authService');
const { authenticate } = require('../middleware/auth');

// POST /api/auth/register
router.post('/register', async (req, res) => {
    try {
        const { username, password, displayName, role } = req.body;
        if (!username || !password || !displayName) {
            return res.status(400).json({ success: false, message: 'Thiếu thông tin đăng ký' });
        }
        const result = await authService.register(username, password, displayName, role || 'customer');
        res.status(201).json({ success: true, data: result });
    } catch (err) {
        res.status(400).json({ success: false, message: err.message });
    }
});

// POST /api/auth/login
router.post('/login', async (req, res) => {
    try {
        const { username, password } = req.body;
        if (!username || !password) {
            return res.status(400).json({ success: false, message: 'Thiếu username hoặc password' });
        }
        const result = await authService.login(username, password);
        res.json({ success: true, data: result });
    } catch (err) {
        res.status(401).json({ success: false, message: err.message });
    }
});

// GET /api/auth/me
router.get('/me', authenticate, async (req, res) => {
    try {
        const user = await authService.getUserById(req.user.id);
        if (!user) return res.status(404).json({ success: false, message: 'User not found' });
        res.json({ success: true, data: user });
    } catch (err) {
        res.status(500).json({ success: false, message: err.message });
    }
});

module.exports = router;
