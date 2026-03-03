require('dotenv').config();

const express = require('express');
const http = require('http');
const cors = require('cors');
const { Server } = require('socket.io');

const connectDB = require('./src/config/database');
const routes = require('./src/routes');
const errorHandler = require('./src/middleware/errorHandler');
const setupSocket = require('./src/socket/socketHandler');

const app = express();
const server = http.createServer(app);
const io = new Server(server, {
    cors: {
        origin: '*',
        methods: ['GET', 'POST', 'PUT', 'DELETE']
    }
});

// Middleware
app.use(cors());
app.use(express.json());
app.use(express.urlencoded({ extended: true }));

// Share io instance with controllers
app.set('io', io);

// Routes
app.use('/api', routes);

// Health check
app.get('/health', (req, res) => {
    res.json({ status: 'OK', timestamp: new Date().toISOString() });
});

// Error handler
app.use(errorHandler);

// Socket.IO
setupSocket(io);

// Hardware daemon
const hardwareService = require('./src/services/hardwareService');

// Start server
const PORT = process.env.PORT || 5000;

connectDB().then(async () => {
    // Auto-seed demo map if empty
    try {
        const MapPoint = require('./src/entities/MapPoint');
        const count = await MapPoint.countDocuments();
        if (count === 0) {
            const mapService = require('./src/services/mapService');
            await mapService.seedDemoMap();
            console.log('🗺️  Demo map seeded');
        }
    } catch (e) {
        console.error('Map seed error:', e.message);
    }

    // Auto-seed default users if empty
    try {
        const User = require('./src/entities/User');
        const userCount = await User.countDocuments();
        if (userCount === 0) {
            const defaultUsers = [
                { username: 'admin', password: 'admin', displayName: 'Admin', role: 'admin' },
                { username: 'staff1', password: '1234', displayName: 'Staff 01', role: 'staff' },
                { username: 'staff2', password: '1234', displayName: 'Staff 02', role: 'staff' },
                { username: 'customer1', password: '1234', displayName: 'Khách 01', role: 'customer' },
                { username: 'customer2', password: '1234', displayName: 'Khách 02', role: 'customer' },
            ];
            for (const u of defaultUsers) {
                await new User(u).save();
            }
            console.log('👤 Default users seeded');
        }
    } catch (e) {
        console.error('User seed error:', e.message);
    }

    server.listen(PORT, '0.0.0.0', () => {
        console.log(`🚀 Server running on port ${PORT}`);
        console.log(`📡 Socket.IO ready`);
        console.log(`📍 API: http://localhost:${PORT}/api`);

        // Auto-start hardware daemon
        if (process.env.ENABLE_HARDWARE !== 'false') {
            setTimeout(() => {
                console.log('🔧 Starting hardware daemon...');
                hardwareService.startDaemon();
            }, 2000);
        }
    });
});

// Graceful shutdown
process.on('SIGTERM', () => {
    hardwareService.stopDaemon();
    process.exit(0);
});
process.on('SIGINT', () => {
    hardwareService.stopDaemon();
    process.exit(0);
});
