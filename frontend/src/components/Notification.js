import React, { useEffect, useState } from 'react';

function Notification({ message, type = 'success', onClose }) {
    const [visible, setVisible] = useState(true);

    useEffect(() => {
        const timer = setTimeout(() => {
            setVisible(false);
            if (onClose) onClose();
        }, 4000);
        return () => clearTimeout(timer);
    }, [onClose]);

    if (!visible) return null;

    return (
        <div className={`notification ${type}`}>
            {message}
        </div>
    );
}

export default Notification;
