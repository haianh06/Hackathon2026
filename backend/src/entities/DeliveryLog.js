const mongoose = require('mongoose');

const deliveryLogSchema = new mongoose.Schema({
    order: {
        type: mongoose.Schema.Types.ObjectId,
        ref: 'Order',
        required: true
    },
    customer: {
        type: mongoose.Schema.Types.ObjectId,
        ref: 'User',
        required: true
    },
    staff: {
        type: mongoose.Schema.Types.ObjectId,
        ref: 'User',
        required: true
    },
    destinationPoint: {
        type: String,
        required: true
    },
    startAt: {
        type: Date,
        default: null
    },
    endAt: {
        type: Date,
        default: null
    },
    timeProcess: {
        type: Number, // seconds
        default: null
    },
    status: {
        type: String,
        enum: ['pending', 'in-progress', 'completed', 'failed'],
        default: 'pending'
    }
}, { timestamps: true });

module.exports = mongoose.model('DeliveryLog', deliveryLogSchema);
