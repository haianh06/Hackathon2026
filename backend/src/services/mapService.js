const MapPoint = require('../entities/MapPoint');

class MapService {
    async getAllPoints() {
        return await MapPoint.find();
    }

    async getDestinations() {
        return await MapPoint.find({ type: 'destination' });
    }

    async getPointById(pointId) {
        return await MapPoint.findOne({ pointId });
    }

    async getWarehouse() {
        // Start point is the car's home base
        return await MapPoint.findOne({ type: { $in: ['warehouse', 'start'] } });
    }

    // Dijkstra pathfinding between two points
    async findPath(startId, endId) {
        const allPoints = await MapPoint.find();
        const pointMap = {};
        allPoints.forEach(p => { pointMap[p.pointId] = p; });

        if (!pointMap[startId] || !pointMap[endId]) return null;

        const getWeight = (id1, id2) => {
            const p1 = pointMap[id1];
            const p2 = pointMap[id2];
            return Math.sqrt((p1.x - p2.x) ** 2 + (p1.y - p2.y) ** 2);
        };

        const dist = {};
        const prev = {};
        const visited = new Set();
        const pq = [];

        allPoints.forEach(p => { dist[p.pointId] = Infinity; });
        dist[startId] = 0;
        pq.push({ id: startId, dist: 0 });

        while (pq.length > 0) {
            pq.sort((a, b) => a.dist - b.dist);
            const { id: current } = pq.shift();

            if (visited.has(current)) continue;
            visited.add(current);
            if (current === endId) break;

            const currentPoint = pointMap[current];
            if (!currentPoint) continue;

            for (const neighbor of currentPoint.connections) {
                if (visited.has(neighbor) || !pointMap[neighbor]) continue;
                const weight = getWeight(current, neighbor);
                const newDist = dist[current] + weight;
                if (newDist < dist[neighbor]) {
                    dist[neighbor] = newDist;
                    prev[neighbor] = current;
                    pq.push({ id: neighbor, dist: newDist });
                }
            }
        }

        if (dist[endId] === Infinity) return null;

        const path = [];
        let node = endId;
        while (node) {
            path.unshift(pointMap[node]);
            node = prev[node];
        }
        return path;
    }

    // Seed demo map data matching the physical track layout
    // Grid: 3 columns × 6 rows of intersections
    // Outer clockwise loop: → top, ↓ right, ← bottom, ↑ left
    // Inner corridor with center vertical paths
    // Large obstacle at top (destination B), 4 smaller obstacles
    async seedDemoMap() {
        await MapPoint.deleteMany({});

        const demoPoints = [
            // Row 0 (top) — horizontal road across top (→ right)
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
        ];

        await MapPoint.insertMany(demoPoints);
        return demoPoints;
    }
}

module.exports = new MapService();
