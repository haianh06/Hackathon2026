import axios from 'axios';

const API_BASE = process.env.REACT_APP_API_URL || 'http://localhost:5000/api';

const api = axios.create({
    baseURL: API_BASE,
    headers: { 'Content-Type': 'application/json' }
});

// Attach JWT token to every request
api.interceptors.request.use((config) => {
    const token = localStorage.getItem('token');
    if (token) {
        config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
});

api.interceptors.response.use(
    (res) => res,
    (err) => {
        if (err.response && err.response.status === 401) {
            localStorage.removeItem('token');
            localStorage.removeItem('user');
            if (window.location.pathname !== '/login') {
                window.location.href = '/login';
            }
        }
        return Promise.reject(err);
    }
);

// Auth
export const login = (username, password) => api.post('/auth/login', { username, password });
export const register = (data) => api.post('/auth/register', data);
export const getMe = () => api.get('/auth/me');

// Products
export const getProducts = () => api.get('/products');
export const getProductsByCategory = (category) => api.get(`/products/category/${category}`);

// Orders
export const createOrder = (orderData) => api.post('/orders', orderData);
export const getOrders = () => api.get('/orders');
export const getMyOrders = () => api.get('/orders/my');
export const getPendingOrders = () => api.get('/orders/pending');
export const confirmOrder = (id) => api.put(`/orders/${id}/confirm`);
export const markDelivered = (id) => api.put(`/orders/${id}/delivered`);
export const customerConfirmOrder = (id) => api.put(`/orders/${id}/customer-confirm`);
export const cancelOrder = (id) => api.put(`/orders/${id}/cancel`);

// Map
export const getMapPoints = () => api.get('/map/points');
export const getDestinations = () => api.get('/map/destinations');
export const findPath = (from, to) => api.get(`/map/path?from=${from}&to=${to}`);
export const seedMap = () => api.post('/map/seed');

// Vehicle
export const getVehicleStatus = () => api.get('/vehicle/status');

// Hardware
export const getHardwareStatus = () => api.get('/hardware/status');
export const detectHardware = () => api.get('/hardware/detect');
export const sendMotorCommand = (command, speed) => api.post('/hardware/motor', { command, speed });
export const navigateVehicle = (path) => api.post('/hardware/navigate', { path });
export const startHardwareDaemon = () => api.post('/hardware/daemon/start');
export const stopHardwareDaemon = () => api.post('/hardware/daemon/stop');

// Delivery Logs
export const getDeliveryLogs = (params) => api.get('/delivery-logs', { params });
export const getDeliveryStats = () => api.get('/delivery-logs/stats');
export const getDeliveryLogByOrder = (orderId) => api.get(`/delivery-logs/order/${orderId}`);

// Notifications
export const getNotifications = (params) => api.get('/notifications', { params });
export const getUnreadNotificationCount = () => api.get('/notifications/unread-count');
export const markNotificationRead = (id) => api.put(`/notifications/${id}/read`);
export const markAllNotificationsRead = () => api.put('/notifications/read-all');

export default api;
