const Vehicle = require('../entities/Vehicle');

class VehicleService {
    async getVehicle() {
        let vehicle = await Vehicle.findOne({ vehicleId: 'VEHICLE_01' });
        if (!vehicle) {
            vehicle = await Vehicle.create({
                vehicleId: 'VEHICLE_01',
                currentPosition: 'S',
                status: 'idle'
            });
        }
        return vehicle;
    }

    async updatePosition(pointId) {
        return await Vehicle.findOneAndUpdate(
            { vehicleId: 'VEHICLE_01' },
            { currentPosition: pointId },
            { new: true }
        );
    }

    async updateStatus(status) {
        return await Vehicle.findOneAndUpdate(
            { vehicleId: 'VEHICLE_01' },
            { status },
            { new: true }
        );
    }

    async startDelivery(orderId, destinationPoint) {
        return await Vehicle.findOneAndUpdate(
            { vehicleId: 'VEHICLE_01' },
            {
                status: 'delivering',
                currentOrder: orderId
            },
            { new: true }
        );
    }

    async completeDelivery() {
        return await Vehicle.findOneAndUpdate(
            { vehicleId: 'VEHICLE_01' },
            {
                status: 'returning',
                currentOrder: null
            },
            { new: true }
        );
    }

    async returnToWarehouse() {
        return await Vehicle.findOneAndUpdate(
            { vehicleId: 'VEHICLE_01' },
            {
                status: 'idle',
                currentPosition: 'S'
            },
            { new: true }
        );
    }
}

module.exports = new VehicleService();
