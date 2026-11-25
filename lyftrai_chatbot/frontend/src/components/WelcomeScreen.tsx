import React from 'react';
import { Bot, Sparkles } from 'lucide-react';

interface WelcomeScreenProps {
  onSuggestedPrompt: (prompt: string) => void;
}

const SUGGESTED_PROMPTS = [
  "What is the capital of France?",
  "Explain quantum computing in simple terms",
  "Write a Python function to reverse a string",
  "What are the benefits of regular exercise?",
];

export const WelcomeScreen: React.FC<WelcomeScreenProps> = ({ onSuggestedPrompt }) => {
  return (
    <div className="flex flex-col items-center justify-center h-full px-4 py-8">
      <div className="flex items-center justify-center w-20 h-20 bg-gradient-to-br from-purple-600 to-blue-600 rounded-3xl mb-6 shadow-2xl shadow-purple-900/50">
        <Bot className="w-10 h-10 text-white" />
      </div>
      <h1 className="text-3xl font-bold text-white mb-2">Lyftr AI</h1>
      <p className="text-slate-400 text-center mb-10 max-w-md">
        Your intelligent AI assistant powered by Google Gemini. Ask me anything!
      </p>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 w-full max-w-2xl">
        {SUGGESTED_PROMPTS.map((prompt, i) => (
          <button
            key={i}
            onClick={() => onSuggestedPrompt(prompt)}
            className="text-left p-4 bg-slate-800/80 hover:bg-slate-700/80 border border-slate-700/50 hover:border-slate-600 rounded-xl transition-all duration-200 group"
          >
            <div className="flex items-start gap-3">
              <Sparkles className="w-4 h-4 text-purple-400 mt-0.5 flex-shrink-0 group-hover:text-purple-300" />
              <span className="text-sm text-slate-300 group-hover:text-white transition-colors">{prompt}</span>
            </div>
          </button>
        ))}
      </div>
    </div>
  );
};
