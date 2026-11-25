import React from 'react';
import { MessageMetadata } from '../types';
import { Zap, Brain, Clock, Hash } from 'lucide-react';

interface MetadataBadgeProps {
  metadata: MessageMetadata;
}

export const MetadataBadge: React.FC<MetadataBadgeProps> = ({ metadata }) => {
  const isCache = metadata.resolution_source === 'LLM_CACHE';

  return (
    <div className="flex flex-wrap gap-2 mt-2 text-xs">
      <span
        data-testid="meta-source"
        className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full font-medium ${
          isCache
            ? 'bg-green-900/50 text-green-400 border border-green-800/50'
            : 'bg-blue-900/50 text-blue-400 border border-blue-800/50'
        }`}
      >
        <Zap className="w-3 h-3" />
        {metadata.resolution_source}
      </span>
      <span
        data-testid="meta-model"
        className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-slate-700/50 text-slate-400 border border-slate-600/50"
      >
        <Brain className="w-3 h-3" />
        {metadata.model}
      </span>
      <span
        data-testid="meta-latency"
        className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-slate-700/50 text-slate-400 border border-slate-600/50"
      >
        <Clock className="w-3 h-3" />
        {metadata.latency_ms}ms
      </span>
      <span
        data-testid="meta-input-tokens"
        className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-slate-700/50 text-slate-400 border border-slate-600/50"
      >
        <Hash className="w-3 h-3" />
        in:{metadata.input_tokens}
      </span>
      <span
        data-testid="meta-output-tokens"
        className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-slate-700/50 text-slate-400 border border-slate-600/50"
      >
        <Hash className="w-3 h-3" />
        out:{metadata.output_tokens}
      </span>
      {metadata.key_id && (
        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-slate-700/50 text-slate-500 border border-slate-600/50">
          key:{metadata.key_id}
        </span>
      )}
      {metadata.fallback_attempts > 0 && (
        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-orange-900/50 text-orange-400 border border-orange-800/50">
          fallbacks:{metadata.fallback_attempts}
        </span>
      )}
    </div>
  );
};
