const express = require('express');
const router = express.Router();
const mapController = require('../controllers/mapController');

router.get('/points', mapController.getAllPoints);
router.get('/destinations', mapController.getDestinations);
router.get('/path', mapController.findPath);
router.post('/seed', mapController.seedMap);

module.exports = router;
