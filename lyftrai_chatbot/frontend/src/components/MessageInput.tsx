import React, { useRef, useEffect } from 'react';
import { Send, Loader2 } from 'lucide-react';

interface MessageInputProps {
  onSend: (message: string) => void;
  isLoading: boolean;
  disabled?: boolean;
  value: string;
  onChange: (value: string) => void;
}

export const MessageInput: React.FC<MessageInputProps> = ({
  onSend,
  isLoading,
  disabled,
  value,
  onChange,
}) => {
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
      textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 120)}px`;
    }
  }, [value]);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleSend = () => {
    const trimmed = value.trim();
    if (!trimmed || isLoading || disabled) return;
    onSend(trimmed);
  };

  return (
    <div className="border-t border-slate-800 p-4 bg-slate-900">
      <div className="max-w-4xl mx-auto">
        <div className="flex items-end gap-3 bg-slate-800 border border-slate-700 rounded-2xl px-4 py-3 focus-within:border-indigo-500 transition-colors">
          <textarea
            ref={textareaRef}
            data-testid="chat-input"
            value={value}
            onChange={(e) => onChange(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Message Lyftr AI... (Enter to send, Shift+Enter for newline)"
            disabled={isLoading || disabled}
            className="flex-1 bg-transparent text-slate-100 text-sm placeholder-slate-500 resize-none focus:outline-none disabled:opacity-50 max-h-30 min-h-[24px]"
            rows={1}
          />
          <button
            data-testid="send-button"
            onClick={handleSend}
            disabled={!value.trim() || isLoading || disabled}
            className="flex-shrink-0 p-2 bg-indigo-600 hover:bg-indigo-500 disabled:bg-slate-700 disabled:cursor-not-allowed text-white rounded-xl transition-colors"
          >
            {isLoading ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <Send className="w-4 h-4" />
            )}
          </button>
        </div>
        <p className="text-xs text-slate-600 mt-2 text-center">
          Lyftr AI can make mistakes. Verify important information.
        </p>
      </div>
    </div>
  );
};
