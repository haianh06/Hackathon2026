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
// Start (top-right), Stop (bottom-left), Destinations: A, B, C
db.map_points.insertMany([
    // Row 0 (top)
    { pointId: 'S', x: 550, y: 50, label: 'Start (Xuất phát)', type: 'start', connections: ['B', 'R1'] },
    { pointId: 'B', x: 300, y: 50, label: 'Điểm B', type: 'destination', connections: ['S', 'TL'] },
    { pointId: 'TL', x: 50, y: 50, label: 'Góc trên trái', type: 'waypoint', connections: ['B', 'P4'] },
    // Row 1 (upper-mid)
    { pointId: 'R1', x: 550, y: 280, label: 'Ngã tư phải trên', type: 'intersection', connections: ['S', 'P3', 'C'] },
    { pointId: 'P3', x: 300, y: 280, label: 'Ngã tư trung tâm', type: 'intersection', connections: ['R1', 'P4', 'P5'] },
    { pointId: 'P4', x: 50, y: 280, label: 'Ngã tư trái trên', type: 'intersection', connections: ['TL', 'P3', 'A'] },
    // Row 2 (lower-mid)
    { pointId: 'C', x: 550, y: 460, label: 'Điểm C', type: 'destination', connections: ['R1', 'P5', 'P6'] },
    { pointId: 'P5', x: 300, y: 510, label: 'Ngã tư trung tâm dưới', type: 'intersection', connections: ['P3', 'C', 'A', 'P7'] },
    { pointId: 'A', x: 150, y: 510, label: 'Điểm A', type: 'destination', connections: ['P4', 'P5', 'ST'] },
    // Row 3 (bottom)
    { pointId: 'P6', x: 550, y: 740, label: 'Góc dưới phải', type: 'waypoint', connections: ['C', 'P7'] },
    { pointId: 'P7', x: 300, y: 740, label: 'Dưới trung tâm', type: 'waypoint', connections: ['P5', 'P6', 'ST'] },
    { pointId: 'ST', x: 50, y: 740, label: 'Stop (Kết thúc)', type: 'stop', connections: ['A', 'P7'] },
]);

print('Database seeded successfully!');
