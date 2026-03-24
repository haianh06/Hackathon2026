const express = require('express');
const router = express.Router();
const hardwareController = require('../controllers/hardwareController');

router.get('/status', hardwareController.getStatus);
router.get('/detect', hardwareController.detectHardware);
router.post('/motor', hardwareController.sendMotorCommand);
router.post('/navigate', hardwareController.navigate);
router.post('/daemon/start', hardwareController.startDaemon);
router.post('/daemon/stop', hardwareController.stopDaemon);

// Road Sign Detection (C++ container → Node.js → Socket.IO → React)
router.post('/sign-detections', hardwareController.receiveSignDetections);
router.post('/sign-detect-result', hardwareController.receiveSignDetectResult);
router.post('/sign-detect/:action', hardwareController.signDetectControl);
router.get('/sign-detect/:action', hardwareController.signDetectControl);

module.exports = router;
