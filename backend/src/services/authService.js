const jwt = require('jsonwebtoken');
const User = require('../entities/User');

const JWT_SECRET = process.env.JWT_SECRET || 'hackathon-delivery-bot-2026';
const JWT_EXPIRES = '7d';

class AuthService {
    generateToken(user) {
        return jwt.sign(
            { id: user._id, username: user.username, role: user.role },
            JWT_SECRET,
            { expiresIn: JWT_EXPIRES }
        );
    }

    async register(username, password, displayName, role = 'customer') {
        const existing = await User.findOne({ username });
        if (existing) throw new Error('Username đã tồn tại');
        const user = new User({ username, password, displayName, role });
        await user.save();
        const token = this.generateToken(user);
        return { user: user.toJSON(), token };
    }

    async login(username, password) {
        const user = await User.findOne({ username, isActive: true });
        if (!user) throw new Error('Tài khoản không tồn tại');
        const isMatch = await user.comparePassword(password);
        if (!isMatch) throw new Error('Mật khẩu không đúng');
        const token = this.generateToken(user);
        return { user: user.toJSON(), token };
    }

    async getUserById(id) {
        return User.findById(id).select('-password');
    }

    async getAllUsers() {
        return User.find().select('-password').sort({ createdAt: -1 });
    }
}

module.exports = new AuthService();
