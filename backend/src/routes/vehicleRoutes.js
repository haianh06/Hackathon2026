const express = require('express');
const router = express.Router();
const vehicleController = require('../controllers/vehicleController');

router.get('/status', vehicleController.getStatus);
router.put('/position', vehicleController.updatePosition);
router.post('/deliver', vehicleController.startDelivery);
router.post('/complete', vehicleController.completeDelivery);
router.post('/return', vehicleController.returnToBase);

module.exports = router;
