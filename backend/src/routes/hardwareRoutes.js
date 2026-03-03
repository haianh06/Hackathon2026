const express = require('express');
const router = express.Router();
const hardwareController = require('../controllers/hardwareController');

router.get('/status', hardwareController.getStatus);
router.get('/detect', hardwareController.detectHardware);
router.post('/motor', hardwareController.sendMotorCommand);
router.post('/navigate', hardwareController.navigate);
router.post('/daemon/start', hardwareController.startDaemon);
router.post('/daemon/stop', hardwareController.stopDaemon);

module.exports = router;
