import axios, { AxiosError } from 'axios';
import { ChatRequest, ChatResponse, MessageMetadata } from '../types';

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

const apiClient = axios.create({
  baseURL: API_URL,
  timeout: 60000,
  headers: {
    'Content-Type': 'application/json',
  },
});

export async function sendMessage(
  request: ChatRequest,
  simulateFailure?: string,
  signal?: AbortSignal
): Promise<ChatResponse> {
  const headers: Record<string, string> = {};
  if (simulateFailure) {
    headers['X-Simulate-Failure'] = simulateFailure;
  }
  const response = await apiClient.post<ChatResponse>('/chat', request, {
    headers,
    signal,
  });
  return response.data;
}

export async function getSessionMessages(sessionId: string): Promise<{
  session_id: string;
  messages: Array<{ role: string; content: string; metadata?: MessageMetadata }>;
}> {
  const encodedId = encodeURIComponent(sessionId);
  const response = await apiClient.get(`/sessions/${encodedId}/messages`);
  return response.data;
}

export async function checkHealth(): Promise<{
  status: string;
  keys_configured: number;
  cache_enabled: boolean;
}> {
  const response = await apiClient.get('/health');
  return response.data;
}

export function getErrorMessage(error: unknown): string {
  if (axios.isCancel(error)) {
    return 'Request was canceled.';
  }

  const isAxiosLike =
    axios.isAxiosError(error) ||
    (error !== null &&
      typeof error === 'object' &&
      ('response' in error || 'code' in error || 'isAxiosError' in error));

  if (isAxiosLike) {
    const err = error as AxiosError<any>;
    if (err.response?.status === 503) {
      return 'All AI providers are currently unavailable. Please try again later.';
    }
    if (err.response?.status === 404) {
      return 'Conversation session not found.';
    }
    if (err.response?.status === 422) {
      const detail = err.response.data?.detail;
      if (Array.isArray(detail)) {
        return detail.map((d: any) => d?.msg || JSON.stringify(d)).join(', ');
      }
      return detail || 'Invalid request. Please check your input.';
    }
    if (err.response?.status === 400) {
      const detail = err.response.data?.detail;
      return detail || 'Bad request. Please check your parameters.';
    }
    if (err.response?.data?.detail) {
      return String(err.response.data.detail);
    }
    if (err.code === 'ECONNABORTED') {
      return 'Request timed out. The AI is taking too long to respond.';
    }
    if (!err.response) {
      return 'Cannot connect to the backend. Please check if the server is running.';
    }
  }

  if (error instanceof Error) {
    return error.message;
  }
  return 'An unexpected error occurred. Please try again.';
}
