const express = require('express');
const router = express.Router();
const orderController = require('../controllers/orderController');
const { authenticate, authorize, optionalAuth } = require('../middleware/auth');

router.post('/', optionalAuth, orderController.create);
router.get('/', authenticate, authorize('staff', 'admin'), orderController.getAll);
router.get('/pending', authenticate, authorize('staff', 'admin'), orderController.getPending);
router.get('/my', authenticate, orderController.getMyOrders);
router.get('/:id', optionalAuth, orderController.getById);
router.put('/:id/confirm', authenticate, authorize('staff', 'admin'), orderController.confirm);
router.post('/batch-confirm', authenticate, authorize('staff', 'admin'), orderController.batchConfirm);
router.put('/:id/delivered', authenticate, authorize('staff', 'admin'), orderController.markDelivered);
router.put('/:id/customer-confirm', authenticate, orderController.customerConfirm);
router.put('/:id/cancel', authenticate, authorize('staff', 'admin'), orderController.cancel);

module.exports = router;
