const express = require('express');
const router = express.Router();

const authRoutes = require('./authRoutes');
const productRoutes = require('./productRoutes');
const orderRoutes = require('./orderRoutes');
const mapRoutes = require('./mapRoutes');
const vehicleRoutes = require('./vehicleRoutes');
const hardwareRoutes = require('./hardwareRoutes');
const deliveryLogRoutes = require('./deliveryLogRoutes');
const notificationRoutes = require('./notificationRoutes');
const rfidRoutes = require('./rfidRoutes');

router.use('/auth', authRoutes);
router.use('/products', productRoutes);
router.use('/orders', orderRoutes);
router.use('/map', mapRoutes);
router.use('/vehicle', vehicleRoutes);
router.use('/hardware', hardwareRoutes);
router.use('/delivery-logs', deliveryLogRoutes);
router.use('/notifications', notificationRoutes);
router.use('/rfid', rfidRoutes);

module.exports = router;
