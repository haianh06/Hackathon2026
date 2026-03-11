const mongoose = require('mongoose');

const vehicleSchema = new mongoose.Schema({
    vehicleId: {
        type: String,
        required: true,
        unique: true,
        default: 'VEHICLE_01'
    },
    currentPosition: {
        type: String,
        default: 'S'
    },
    status: {
        type: String,
        enum: ['idle', 'moving', 'delivering', 'returning'],
        default: 'idle'
    },
    currentOrder: {
        type: mongoose.Schema.Types.ObjectId,
        ref: 'Order',
        default: null
    },
    heading: {
        type: [Number],
        default: null
    },
    batteryLevel: {
        type: Number,
        default: 100
    },
    cameraActive: {
        type: Boolean,
        default: false
    }
}, { timestamps: true });

module.exports = mongoose.model('Vehicle', vehicleSchema);
