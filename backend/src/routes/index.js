const express = require('express');
const router = express.Router();

const authRoutes = require('./authRoutes');
const productRoutes = require('./productRoutes');
const orderRoutes = require('./orderRoutes');
const mapRoutes = require('./mapRoutes');
const vehicleRoutes = require('./vehicleRoutes');
const hardwareRoutes = require('./hardwareRoutes');
const deliveryLogRoutes = require('./deliveryLogRoutes');

router.use('/auth', authRoutes);
router.use('/products', productRoutes);
router.use('/orders', orderRoutes);
router.use('/map', mapRoutes);
router.use('/vehicle', vehicleRoutes);
router.use('/hardware', hardwareRoutes);
router.use('/delivery-logs', deliveryLogRoutes);

module.exports = router;
