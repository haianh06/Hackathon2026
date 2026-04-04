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
    // Grid: 3 columns × 4 rows — single-lane, bidirectional
    // All roads are free movement, dashed line markings
    async seedDemoMap() {
        await MapPoint.deleteMany({});

        const demoPoints = [
            // Row 0 (top) — horizontal road
            { pointId: 'TL', x: 200, y: 200, label: 'Góc trên trái', type: 'waypoint', connections: ['B', 'P1'] },
            { pointId: 'B', x: 1200, y: 200, label: 'Điểm B', type: 'destination', connections: ['TL', 'S'] },
            { pointId: 'S', x: 2200, y: 200, label: 'Start (Xuất phát)', type: 'start', connections: ['B', 'P3'] },

            // Row 1 (below top building) — horizontal road
            { pointId: 'P1', x: 200, y: 940, label: 'Ngã tư trái', type: 'intersection', connections: ['TL', 'P2', 'A'] },
            { pointId: 'P2', x: 1200, y: 940, label: 'Ngã tư trung tâm trên', type: 'intersection', connections: ['P1', 'P3', 'P4'] },
            { pointId: 'P3', x: 2200, y: 940, label: 'Ngã tư phải', type: 'intersection', connections: ['S', 'P2', 'C'] },

            // Row 2 (between middle and bottom buildings) — horizontal road
            { pointId: 'A', x: 200, y: 1680, label: 'Điểm A', type: 'destination', connections: ['P1', 'P4', 'ST'] },
            { pointId: 'P4', x: 1200, y: 1680, label: 'Ngã tư trung tâm dưới', type: 'intersection', connections: ['P2', 'A', 'C', 'P5'] },
            { pointId: 'C', x: 2200, y: 1680, label: 'Điểm C', type: 'destination', connections: ['P3', 'P4', 'P6'] },

            // Row 3 (bottom) — horizontal road
            { pointId: 'ST', x: 200, y: 2600, label: 'Stop (Kết thúc)', type: 'stop', connections: ['A', 'P5'] },
            { pointId: 'P5', x: 1200, y: 2600, label: 'Trung gian dưới', type: 'waypoint', connections: ['ST', 'P4', 'P6'] },
            { pointId: 'P6', x: 2200, y: 2600, label: 'Góc dưới phải', type: 'waypoint', connections: ['P5', 'C'] },
        ];

        await MapPoint.insertMany(demoPoints);
        return demoPoints;
    }
}

module.exports = new MapService();
