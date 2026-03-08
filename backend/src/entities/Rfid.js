const mongoose = require('mongoose');

const rfidSchema = new mongoose.Schema({
    rfidId: {
        type: String,
        required: true,
        unique: true,
        trim: true
    },
    name: {
        type: String,
        required: true,
        trim: true
    },
    x: {
        type: Number,
        required: true,
        default: 0
    },
    y: {
        type: Number,
        required: true,
        default: 0
    }
}, {
    timestamps: true
});

module.exports = mongoose.model('Rfid', rfidSchema);
