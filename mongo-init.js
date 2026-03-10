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

// Seed map waypoints — grid layout matching the physical track
// 3 columns × 6 rows of intersections
// Outer clockwise loop: → top, ↓ right, ← bottom, ↑ left
// Inner two-way roads separated by barriers, center vertical path
// Bottom buildings: free two-way (no arrows)
db.map_points.insertMany([
    // Row 0 (top) — horizontal road (→ right)
    { pointId: 'TL', x: 50, y: 30, label: 'Góc trên trái', type: 'waypoint', connections: ['B', 'P1'] },
    { pointId: 'B', x: 300, y: 30, label: 'Điểm B', type: 'destination', connections: ['TL', 'S'] },
    { pointId: 'S', x: 550, y: 30, label: 'Start (Xuất phát)', type: 'start', connections: ['B', 'P3'] },

    // Row 1 (below building) — horizontal road (← left)
    { pointId: 'P1', x: 50, y: 195, label: 'Ngã tư trái 1', type: 'intersection', connections: ['TL', 'P2', 'P4'] },
    { pointId: 'P2', x: 300, y: 195, label: 'Trung gian trên', type: 'waypoint', connections: ['P1', 'P3'] },
    { pointId: 'P3', x: 550, y: 195, label: 'Ngã tư phải 1', type: 'intersection', connections: ['S', 'P2', 'R1'] },

    // Row 2 (upper corridor) — horizontal road (→ right)
    { pointId: 'P4', x: 50, y: 275, label: 'Ngã tư trái 2', type: 'intersection', connections: ['P1', 'P5', 'A'] },
    { pointId: 'P5', x: 300, y: 275, label: 'Ngã tư trung tâm trên', type: 'intersection', connections: ['P4', 'R1', 'P6'] },
    { pointId: 'R1', x: 550, y: 275, label: 'Ngã tư phải 2', type: 'intersection', connections: ['P5', 'P3', 'C'] },

    // Row 3 (lower corridor) — horizontal road (← left)
    { pointId: 'A', x: 50, y: 410, label: 'Điểm A', type: 'destination', connections: ['P4', 'P6', 'P7'] },
    { pointId: 'P6', x: 300, y: 410, label: 'Ngã tư trung tâm dưới', type: 'intersection', connections: ['A', 'C', 'P5'] },
    { pointId: 'C', x: 550, y: 410, label: 'Điểm C', type: 'destination', connections: ['R1', 'P6', 'P9'] },

    // Row 4 (below lower barrier) — horizontal road (→ right)
    { pointId: 'P7', x: 50, y: 490, label: 'Góc trái dưới', type: 'waypoint', connections: ['A', 'P8', 'ST'] },
    { pointId: 'P8', x: 300, y: 490, label: 'Ngã tư dưới giữa', type: 'intersection', connections: ['P7', 'P9', 'P10'] },
    { pointId: 'P9', x: 550, y: 490, label: 'Ngã tư phải dưới', type: 'intersection', connections: ['P8', 'C', 'P11'] },

    // Row 5 (bottom) — horizontal road (← left)
    { pointId: 'ST', x: 50, y: 668, label: 'Stop (Kết thúc)', type: 'stop', connections: ['P7', 'P10'] },
    { pointId: 'P10', x: 300, y: 668, label: 'Trung gian dưới', type: 'waypoint', connections: ['ST', 'P11', 'P8'] },
    { pointId: 'P11', x: 550, y: 668, label: 'Góc dưới phải', type: 'waypoint', connections: ['P10', 'P9'] },
]);

print('Database seeded successfully!');
