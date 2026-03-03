const Product = require('../entities/Product');

class ProductService {
    async getAllProducts() {
        return await Product.find({ inStock: true }).sort({ category: 1, name: 1 });
    }

    async getProductById(id) {
        return await Product.findById(id);
    }

    async getProductsByCategory(category) {
        return await Product.find({ category, inStock: true });
    }
}

module.exports = new ProductService();
