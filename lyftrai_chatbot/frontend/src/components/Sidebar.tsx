import React from 'react';
import { Bot, Plus, Settings, ChevronDown, ChevronUp } from 'lucide-react';

interface SidebarProps {
  sessionId: string;
  systemPrompt: string;
  onSystemPromptChange: (prompt: string) => void;
  onNewChat: () => void;
  isOpen?: boolean;
}

export const Sidebar: React.FC<SidebarProps> = ({
  sessionId,
  systemPrompt,
  onSystemPromptChange,
  onNewChat,
}) => {
  const [showSystemPrompt, setShowSystemPrompt] = React.useState(false);

  return (
    <div className="w-64 bg-slate-900 border-r border-slate-800 flex flex-col h-full">
      {/* Logo */}
      <div className="flex items-center gap-3 p-4 border-b border-slate-800">
        <div className="w-8 h-8 bg-gradient-to-br from-purple-600 to-blue-600 rounded-lg flex items-center justify-center">
          <Bot className="w-5 h-5 text-white" />
        </div>
        <span className="text-lg font-bold text-white">Lyftr AI</span>
      </div>

      {/* New Chat Button */}
      <div className="p-3">
        <button
          onClick={onNewChat}
          className="w-full flex items-center gap-2 px-3 py-2.5 bg-indigo-600 hover:bg-indigo-500 text-white rounded-xl transition-colors font-medium text-sm"
        >
          <Plus className="w-4 h-4" />
          New Chat
        </button>
      </div>

      {/* System Prompt Section */}
      <div className="px-3 py-2">
        <button
          onClick={() => setShowSystemPrompt(!showSystemPrompt)}
          className="w-full flex items-center justify-between px-3 py-2 text-slate-400 hover:text-slate-200 hover:bg-slate-800 rounded-lg transition-colors text-sm"
        >
          <div className="flex items-center gap-2">
            <Settings className="w-4 h-4" />
            System Prompt
          </div>
          {showSystemPrompt ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
        </button>
        {showSystemPrompt && (
          <div className="mt-2">
            <textarea
              data-testid="system-prompt-input"
              value={systemPrompt}
              onChange={(e) => onSystemPromptChange(e.target.value)}
              placeholder="You are a helpful assistant..."
              className="w-full bg-slate-800 border border-slate-700 text-slate-200 text-xs rounded-lg p-2.5 resize-none focus:outline-none focus:border-indigo-500 placeholder-slate-500"
              rows={4}
            />
          </div>
        )}
      </div>

      {/* Spacer */}
      <div className="flex-1" />

      {/* Session Info */}
      <div className="p-3 border-t border-slate-800">
        <p className="text-xs text-slate-600 font-mono truncate">Session: {sessionId}</p>
      </div>
    </div>
  );
};
