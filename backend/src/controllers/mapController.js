const mapService = require('../services/mapService');

class MapController {
    async getAllPoints(req, res) {
        try {
            const points = await mapService.getAllPoints();
            res.json({ success: true, data: points });
        } catch (error) {
            res.status(500).json({ success: false, message: error.message });
        }
    }

    async getDestinations(req, res) {
        try {
            const destinations = await mapService.getDestinations();
            res.json({ success: true, data: destinations });
        } catch (error) {
            res.status(500).json({ success: false, message: error.message });
        }
    }

    async findPath(req, res) {
        try {
            const { from, to } = req.query;
            if (!from || !to) {
                return res.status(400).json({ success: false, message: 'Cần cung cấp điểm đi và điểm đến' });
            }
            const path = await mapService.findPath(from, to);
            if (!path) {
                return res.status(404).json({ success: false, message: 'Không tìm thấy đường đi' });
            }
            res.json({ success: true, data: path });
        } catch (error) {
            res.status(500).json({ success: false, message: error.message });
        }
    }

    async seedMap(req, res) {
        try {
            const points = await mapService.seedDemoMap();
            res.json({ success: true, data: points, message: 'Demo map seeded successfully' });
        } catch (error) {
            res.status(500).json({ success: false, message: error.message });
        }
    }
}

module.exports = new MapController();
