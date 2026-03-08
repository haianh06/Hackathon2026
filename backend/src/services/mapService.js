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
            { pointId: 'TL', x: 50, y: 35, label: 'Góc trên trái', type: 'waypoint', connections: ['B', 'P1'] },
            { pointId: 'B', x: 300, y: 35, label: 'Điểm B', type: 'destination', connections: ['TL', 'S'] },
            { pointId: 'S', x: 550, y: 35, label: 'Start (Xuất phát)', type: 'start', connections: ['B', 'P2'] },

            // Row 1 (below rectangle) — horizontal road (← left)
            { pointId: 'P1', x: 50, y: 210, label: 'Dưới rect trái', type: 'waypoint', connections: ['TL', 'P2', 'P3'] },
            { pointId: 'P2', x: 550, y: 210, label: 'Dưới rect phải', type: 'waypoint', connections: ['S', 'P1', 'R1'] },

            // Row 2 (upper corridor) — horizontal road (→ right)
            { pointId: 'P3', x: 50, y: 300, label: 'Ngã tư trái trên', type: 'intersection', connections: ['P1', 'P4', 'A'] },
            { pointId: 'P4', x: 300, y: 300, label: 'Ngã tư trung tâm', type: 'intersection', connections: ['P3', 'R1', 'P5'] },
            { pointId: 'R1', x: 550, y: 300, label: 'Ngã tư phải trên', type: 'intersection', connections: ['P2', 'P4', 'C'] },

            // Row 3 (lower corridor) — horizontal road (← left)
            { pointId: 'A', x: 50, y: 470, label: 'Điểm A', type: 'destination', connections: ['P3', 'P5', 'P7'] },
            { pointId: 'P5', x: 300, y: 470, label: 'Ngã tư trung tâm dưới', type: 'intersection', connections: ['P4', 'A', 'C'] },
            { pointId: 'C', x: 550, y: 470, label: 'Điểm C', type: 'destination', connections: ['R1', 'P5', 'P8'] },

            // Row 4 (below lower barrier) — horizontal road (→ right)
            { pointId: 'P7', x: 50, y: 545, label: 'Dưới barrier trái', type: 'waypoint', connections: ['A', 'P8', 'ST'] },
            { pointId: 'P8', x: 550, y: 545, label: 'Dưới barrier phải', type: 'waypoint', connections: ['C', 'P7', 'P6'] },

            // Row 5 (bottom) — horizontal road (← left)
            { pointId: 'ST', x: 50, y: 770, label: 'Stop (Kết thúc)', type: 'stop', connections: ['P7', 'P9'] },
            { pointId: 'P9', x: 300, y: 770, label: 'Dưới trung tâm', type: 'waypoint', connections: ['ST', 'P6'] },
            { pointId: 'P6', x: 550, y: 770, label: 'Góc dưới phải', type: 'waypoint', connections: ['P8', 'P9'] },
        ];

        await MapPoint.insertMany(demoPoints);
        return demoPoints;
    }
}

module.exports = new MapService();
