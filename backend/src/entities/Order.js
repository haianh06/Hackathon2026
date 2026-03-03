const mongoose = require('mongoose');

const orderItemSchema = new mongoose.Schema({
    product: {
        type: mongoose.Schema.Types.ObjectId,
        ref: 'Product',
        required: true
    },
    name: String,
    price: Number,
    quantity: {
        type: Number,
        required: true,
        min: 1,
        default: 1
    }
}, { _id: false });

const orderSchema = new mongoose.Schema({
    items: [orderItemSchema],
    totalPrice: {
        type: Number,
        required: true,
        min: 0
    },
    status: {
        type: String,
        enum: ['pending', 'confirmed', 'delivering', 'delivered', 'cancelled'],
        default: 'pending'
    },
    customer: {
        type: mongoose.Schema.Types.ObjectId,
        ref: 'User',
        default: null
    },
    customerName: {
        type: String,
        default: 'Khách hàng'
    },
    confirmedBy: {
        type: mongoose.Schema.Types.ObjectId,
        ref: 'User',
        default: null
    },
    destinationPoint: {
        type: String,
        required: true
    },
    vehicleStatus: {
        type: String,
        enum: ['idle', 'moving', 'arrived'],
        default: 'idle'
    },
    note: {
        type: String,
        default: ''
    }
}, { timestamps: true });

module.exports = mongoose.model('Order', orderSchema);
