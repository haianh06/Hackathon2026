const productService = require('../services/productService');

class ProductController {
    async getAll(req, res) {
        try {
            const products = await productService.getAllProducts();
            res.json({ success: true, data: products });
        } catch (error) {
            res.status(500).json({ success: false, message: error.message });
        }
    }

    async getById(req, res) {
        try {
            const product = await productService.getProductById(req.params.id);
            if (!product) {
                return res.status(404).json({ success: false, message: 'Không tìm thấy sản phẩm' });
            }
            res.json({ success: true, data: product });
        } catch (error) {
            res.status(500).json({ success: false, message: error.message });
        }
    }

    async getByCategory(req, res) {
        try {
            const products = await productService.getProductsByCategory(req.params.category);
            res.json({ success: true, data: products });
        } catch (error) {
            res.status(500).json({ success: false, message: error.message });
        }
    }
}

module.exports = new ProductController();
