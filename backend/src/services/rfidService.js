const Rfid = require('../entities/Rfid');

class RfidService {
    async getAll() {
        return await Rfid.find().sort({ name: 1 });
    }

    async getByRfidId(rfidId) {
        return await Rfid.findOne({ rfidId });
    }

    async getById(id) {
        return await Rfid.findById(id);
    }

    async createOrUpdate(rfidId, data) {
        const existing = await Rfid.findOne({ rfidId });
        if (existing) {
            existing.name = data.name ?? existing.name;
            existing.x = data.x ?? existing.x;
            existing.y = data.y ?? existing.y;
            return await existing.save();
        }
        return await Rfid.create({ rfidId, ...data });
    }

    async deleteByRfidId(rfidId) {
        return await Rfid.findOneAndDelete({ rfidId });
    }
}

module.exports = new RfidService();
