db = db.getSiblingDB('delivery_bot');

// Create collections
db.createCollection('products');
db.createCollection('orders');
db.createCollection('map_points');
db.createCollection('users');
db.createCollection('deliverylogs');

// NOTE: Users are seeded by the backend server.js on startup with proper bcrypt hashing.
// Default accounts: admin/admin, staff1/1234, staff2/1234, customer1/1234, customer2/1234

// Seed sample products
db.products.insertMany([
    {
        name: 'Nước suối',
        description: 'Nước suối tinh khiết 500ml',
        price: 10000,
        image: '💧',
        category: 'drink',
        inStock: true
    },
    {
        name: 'Coca Cola',
        description: 'Coca Cola lon 330ml',
        price: 15000,
        image: '🥤',
        category: 'drink',
        inStock: true
    },
    {
        name: 'Bánh mì',
        description: 'Bánh mì thịt nguội',
        price: 20000,
        image: '🥖',
        category: 'food',
        inStock: true
    },
    {
        name: 'Snack khoai tây',
        description: 'Snack khoai tây giòn rụm',
        price: 12000,
        image: '🍟',
        category: 'food',
        inStock: true
    },
    {
        name: 'Cà phê',
        description: 'Cà phê sữa đá',
        price: 25000,
        image: '☕',
        category: 'drink',
        inStock: true
    },
    {
        name: 'Trà sữa',
        description: 'Trà sữa trân châu',
        price: 30000,
        image: '🧋',
        category: 'drink',
        inStock: true
    }
]);

// Seed map waypoints — single-lane grid layout matching the physical track
// 3 columns × 4 rows of intersections
// All roads are single-lane, bidirectional (free movement)
// Dashed road markings, white buildings as obstacles
db.map_points.insertMany([
    // Row 0 (top) — horizontal road
    { pointId: 'TL', x: 50, y: 50, label: 'Góc trên trái', type: 'waypoint', connections: ['B', 'P1'] },
    { pointId: 'B', x: 300, y: 50, label: 'Điểm B', type: 'destination', connections: ['TL', 'S'] },
    { pointId: 'S', x: 550, y: 50, label: 'Start (Xuất phát)', type: 'start', connections: ['B', 'P3'] },

    // Row 1 (below top building) — horizontal road
    { pointId: 'P1', x: 50, y: 235, label: 'Ngã tư trái', type: 'intersection', connections: ['TL', 'P2', 'A'] },
    { pointId: 'P2', x: 300, y: 235, label: 'Ngã tư trung tâm trên', type: 'intersection', connections: ['P1', 'P3', 'P4'] },
    { pointId: 'P3', x: 550, y: 235, label: 'Ngã tư phải', type: 'intersection', connections: ['S', 'P2', 'C'] },

    // Row 2 (between middle and bottom buildings) — horizontal road
    { pointId: 'A', x: 50, y: 420, label: 'Điểm A', type: 'destination', connections: ['P1', 'P4', 'ST'] },
    { pointId: 'P4', x: 300, y: 420, label: 'Ngã tư trung tâm dưới', type: 'intersection', connections: ['P2', 'A', 'C', 'P5'] },
    { pointId: 'C', x: 550, y: 420, label: 'Điểm C', type: 'destination', connections: ['P3', 'P4', 'P6'] },

    // Row 3 (bottom) — horizontal road
    { pointId: 'ST', x: 50, y: 650, label: 'Stop (Kết thúc)', type: 'stop', connections: ['A', 'P5'] },
    { pointId: 'P5', x: 300, y: 650, label: 'Trung gian dưới', type: 'waypoint', connections: ['ST', 'P4', 'P6'] },
    { pointId: 'P6', x: 550, y: 650, label: 'Góc dưới phải', type: 'waypoint', connections: ['P5', 'C'] },
]);

print('Database seeded successfully!');
