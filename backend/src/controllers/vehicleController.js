const vehicleService = require('../services/vehicleService');

class VehicleController {
    async getStatus(req, res) {
        try {
            const vehicle = await vehicleService.getVehicle();
            res.json({ success: true, data: vehicle });
        } catch (error) {
            res.status(500).json({ success: false, message: error.message });
        }
    }

    async updatePosition(req, res) {
        try {
            const { pointId } = req.body;
            const vehicle = await vehicleService.updatePosition(pointId);

            const io = req.app.get('io');
            if (io) {
                io.emit('vehicle-position', { pointId, vehicle });
            }

            res.json({ success: true, data: vehicle });
        } catch (error) {
            res.status(500).json({ success: false, message: error.message });
        }
    }

    async startDelivery(req, res) {
        try {
            const { orderId, destinationPoint } = req.body;
            const vehicle = await vehicleService.startDelivery(orderId, destinationPoint);

            const io = req.app.get('io');
            if (io) {
                io.emit('vehicle-delivering', vehicle);
            }

            res.json({ success: true, data: vehicle });
        } catch (error) {
            res.status(500).json({ success: false, message: error.message });
        }
    }

    async completeDelivery(req, res) {
        try {
            const vehicle = await vehicleService.completeDelivery();

            const io = req.app.get('io');
            if (io) {
                io.emit('vehicle-completed', vehicle);
            }

            res.json({ success: true, data: vehicle });
        } catch (error) {
            res.status(500).json({ success: false, message: error.message });
        }
    }

    async returnToBase(req, res) {
        try {
            const vehicle = await vehicleService.returnToWarehouse();

            const io = req.app.get('io');
            if (io) {
                io.emit('vehicle-returned', vehicle);
            }

            res.json({ success: true, data: vehicle });
        } catch (error) {
            res.status(500).json({ success: false, message: error.message });
        }
    }
}

module.exports = new VehicleController();
