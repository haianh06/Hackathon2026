const mongoose = require('mongoose');

const productSchema = new mongoose.Schema({
    name: {
        type: String,
        required: true,
        trim: true
    },
    description: {
        type: String,
        default: ''
    },
    price: {
        type: Number,
        required: true,
        min: 0
    },
    image: {
        type: String,
        default: '📦'
    },
    category: {
        type: String,
        enum: ['food', 'drink', 'other'],
        default: 'other'
    },
    inStock: {
        type: Boolean,
        default: true
    }
}, { timestamps: true });

module.exports = mongoose.model('Product', productSchema);
