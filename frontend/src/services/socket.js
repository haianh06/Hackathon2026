import { io } from 'socket.io-client';

const SOCKET_URL = process.env.REACT_APP_SOCKET_URL || 'http://localhost:5000';

// When behind nginx proxy, use relative path (window.location.origin)
const socketUrl = SOCKET_URL === '/' ? window.location.origin : SOCKET_URL;

const socket = io(socketUrl, {
    autoConnect: true,
    reconnection: true,
    reconnectionDelay: 1000,
    path: '/socket.io/'
});

socket.on('connect', () => {
    console.log('Socket connected:', socket.id);
});

socket.on('disconnect', () => {
    console.log('Socket disconnected');
});

export default socket;
