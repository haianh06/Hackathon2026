const rfidService = require('../services/rfidService');

class RfidController {
    async getAll(req, res) {
        try {
            const rfids = await rfidService.getAll();
            res.json({ success: true, data: rfids });
        } catch (error) {
            res.status(500).json({ success: false, message: error.message });
        }
    }

    async getByRfidId(req, res) {
        try {
            const rfid = await rfidService.getByRfidId(req.params.rfidId);
            if (!rfid) {
                return res.json({ success: true, data: null, isNew: true });
            }
            res.json({ success: true, data: rfid, isNew: false });
        } catch (error) {
            res.status(500).json({ success: false, message: error.message });
        }
    }

    async save(req, res) {
        try {
            const { rfidId, name, x, y } = req.body;
            if (!rfidId || !name) {
                return res.status(400).json({ success: false, message: 'rfidId và name là bắt buộc' });
            }
            const rfid = await rfidService.createOrUpdate(rfidId, { name, x, y });
            res.json({ success: true, data: rfid });
        } catch (error) {
            res.status(500).json({ success: false, message: error.message });
        }
    }

    async delete(req, res) {
        try {
            const result = await rfidService.deleteByRfidId(req.params.rfidId);
            if (!result) {
                return res.status(404).json({ success: false, message: 'Không tìm thấy RFID' });
            }
            res.json({ success: true, message: 'Đã xoá RFID' });
        } catch (error) {
            res.status(500).json({ success: false, message: error.message });
        }
    }
}

module.exports = new RfidController();
