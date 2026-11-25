export interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  metadata?: MessageMetadata;
  timestamp: Date;
  isLoading?: boolean;
}

export interface MessageMetadata {
  resolution_source: 'LIVE_INFERENCE' | 'LLM_CACHE';
  model: string;
  latency_ms: number;
  input_tokens: number;
  output_tokens: number;
  key_id: string | null;
  fallback_attempts: number;
}

export interface ChatRequest {
  session_id: string;
  message: string;
  system_prompt?: string;
}

export interface ChatResponse {
  session_id: string;
  reply: string;
  metadata: MessageMetadata;
}

export interface ApiError {
  detail: string;
  status?: number;
}
