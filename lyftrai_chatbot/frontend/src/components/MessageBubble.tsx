import React from 'react';
import { Message } from '../types';
import { MetadataBadge } from './MetadataBadge';
import { Bot, User } from 'lucide-react';

interface MessageBubbleProps {
  message: Message;
}

const TypingIndicator: React.FC = () => (
  <div className="flex items-center gap-1 px-1 py-2">
    {[0, 1, 2].map((i) => (
      <div
        key={i}
        className="w-2 h-2 bg-slate-400 rounded-full animate-bounce"
        style={{ animationDelay: `${i * 0.15}s`, animationDuration: '1s' }}
      />
    ))}
  </div>
);

export const MessageBubble: React.FC<MessageBubbleProps> = ({ message }) => {
  const isUser = message.role === 'user';

  if (isUser) {
    return (
      <div className="flex justify-end mb-4 animate-fadeIn">
        <div className="flex items-start gap-2 max-w-[80%]">
          <div className="bg-indigo-600 text-white px-4 py-3 rounded-2xl rounded-tr-sm shadow-lg">
            <p className="text-sm leading-relaxed whitespace-pre-wrap">{message.content}</p>
          </div>
          <div className="flex-shrink-0 w-8 h-8 bg-indigo-700 rounded-full flex items-center justify-center mt-1">
            <User className="w-4 h-4 text-white" />
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex justify-start mb-4 animate-fadeIn" data-testid="assistant-message">
      <div className="flex items-start gap-2 max-w-[80%]">
        <div className="flex-shrink-0 w-8 h-8 bg-gradient-to-br from-purple-600 to-blue-600 rounded-full flex items-center justify-center mt-1">
          <Bot className="w-4 h-4 text-white" />
        </div>
        <div>
          <div className="bg-slate-800 text-slate-100 px-4 py-3 rounded-2xl rounded-tl-sm shadow-lg border border-slate-700/50">
            {message.isLoading ? (
              <TypingIndicator />
            ) : (
              <p className="text-sm leading-relaxed whitespace-pre-wrap">{message.content}</p>
            )}
          </div>
          {message.metadata && !message.isLoading && (
            <MetadataBadge metadata={message.metadata} />
          )}
        </div>
      </div>
    </div>
  );
};
