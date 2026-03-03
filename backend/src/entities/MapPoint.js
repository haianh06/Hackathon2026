const mongoose = require('mongoose');

const mapPointSchema = new mongoose.Schema({
    pointId: {
        type: String,
        required: true,
        unique: true
    },
    x: {
        type: Number,
        required: true
    },
    y: {
        type: Number,
        required: true
    },
    label: {
        type: String,
        default: ''
    },
    type: {
        type: String,
        enum: ['warehouse', 'waypoint', 'intersection', 'destination', 'start', 'stop'],
        default: 'waypoint'
    },
    connections: [{
        type: String
    }]
}, { timestamps: true });

module.exports = mongoose.model('MapPoint', mapPointSchema);
