import React, { useEffect, useRef } from 'react';
import { Message } from '../types';
import { MessageBubble } from './MessageBubble';
import { WelcomeScreen } from './WelcomeScreen';

interface ChatWindowProps {
  messages: Message[];
  onSuggestedPrompt: (prompt: string) => void;
}

export const ChatWindow: React.FC<ChatWindowProps> = ({ messages, onSuggestedPrompt }) => {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (typeof bottomRef.current?.scrollIntoView === 'function') {
      bottomRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [messages]);

  if (messages.length === 0) {
    return <WelcomeScreen onSuggestedPrompt={onSuggestedPrompt} />;
  }

  return (
    <div className="flex-1 overflow-y-auto p-4 scrollbar-thin">
      <div className="max-w-4xl mx-auto py-4">
        {messages.map((message) => (
          <MessageBubble key={message.id} message={message} />
        ))}
        <div ref={bottomRef} />
      </div>
    </div>
  );
};
