const express = require('express');
const router = express.Router();
const rfidController = require('../controllers/rfidController');

router.get('/', rfidController.getAll);
router.get('/:rfidId', rfidController.getByRfidId);
router.post('/', rfidController.save);
router.delete('/:rfidId', rfidController.delete);

module.exports = router;
