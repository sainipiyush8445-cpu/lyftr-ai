import React, { useEffect } from 'react';
import { AlertCircle, X } from 'lucide-react';

interface ErrorNotificationProps {
  message: string;
  onDismiss: () => void;
}

export const ErrorNotification: React.FC<ErrorNotificationProps> = ({ message, onDismiss }) => {
  useEffect(() => {
    const timer = setTimeout(onDismiss, 8000);
    return () => clearTimeout(timer);
  }, [onDismiss]);

  return (
    <div className="fixed bottom-4 right-4 max-w-sm bg-red-900/90 border border-red-700 text-red-100 px-4 py-3 rounded-xl shadow-2xl flex items-start gap-3 animate-slideIn z-50">
      <AlertCircle className="w-5 h-5 text-red-400 flex-shrink-0 mt-0.5" />
      <p className="text-sm flex-1">{message}</p>
      <button onClick={onDismiss} className="text-red-400 hover:text-red-200 flex-shrink-0">
        <X className="w-4 h-4" />
      </button>
    </div>
  );
};
