const jwt = require('jsonwebtoken');
const JWT_SECRET = process.env.JWT_SECRET || 'hackathon-delivery-bot-2026';

// Authenticate JWT token
const authenticate = (req, res, next) => {
    const authHeader = req.headers.authorization;
    if (!authHeader || !authHeader.startsWith('Bearer ')) {
        return res.status(401).json({ success: false, message: 'Chưa đăng nhập' });
    }
    try {
        const token = authHeader.split(' ')[1];
        const decoded = jwt.verify(token, JWT_SECRET);
        req.user = decoded;
        next();
    } catch (err) {
        return res.status(401).json({ success: false, message: 'Token không hợp lệ' });
    }
};

// Authorization by role(s)
const authorize = (...roles) => {
    return (req, res, next) => {
        if (!req.user || !roles.includes(req.user.role)) {
            return res.status(403).json({ success: false, message: 'Không có quyền truy cập' });
        }
        next();
    };
};

// Optional auth - doesn't fail if no token
const optionalAuth = (req, res, next) => {
    const authHeader = req.headers.authorization;
    if (authHeader && authHeader.startsWith('Bearer ')) {
        try {
            const token = authHeader.split(' ')[1];
            req.user = jwt.verify(token, JWT_SECRET);
        } catch (err) {
            // ignore invalid token
        }
    }
    next();
};

module.exports = { authenticate, authorize, optionalAuth };
