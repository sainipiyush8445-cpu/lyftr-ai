import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import App from '../App';
import * as client from '../api/client';

describe('Frontend App Component & Persistence', () => {
  beforeEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
    window.HTMLElement.prototype.scrollIntoView = vi.fn();
  });

  it('generates session ID once and reuses it from localStorage', async () => {
    vi.spyOn(client, 'getSessionMessages').mockRejectedValue({
      response: { status: 404 },
    });

    const { unmount } = render(<App />);
    const storedSession = localStorage.getItem('lyftr_session_id');
    expect(storedSession).toBeTruthy();
    expect(storedSession).toMatch(/^s-[a-f0-9]{8}$/);

    unmount();

    // Second render reuses stored session ID
    render(<App />);
    expect(localStorage.getItem('lyftr_session_id')).toBe(storedSession);
  });

  it('restores previous messages from backend on mount', async () => {
    localStorage.setItem('lyftr_session_id', 's-test1234');
    vi.spyOn(client, 'getSessionMessages').mockResolvedValue({
      session_id: 's-test1234',
      messages: [
        { role: 'user', content: 'What is the capital of Italy?' },
        {
          role: 'assistant',
          content: 'The capital of Italy is Rome.',
          metadata: {
            resolution_source: 'LIVE_INFERENCE',
            model: 'gemini/gemini-2.0-flash',
            latency_ms: 250,
            input_tokens: 15,
            output_tokens: 8,
            key_id: 'key_1',
            fallback_attempts: 0,
          },
        },
      ],
    });

    render(<App />);

    await waitFor(() => {
      expect(screen.getByText('What is the capital of Italy?')).toBeTruthy();
      expect(screen.getByText('The capital of Italy is Rome.')).toBeTruthy();
    });
  });

  it('handles 404 on restore by starting with empty messages', async () => {
    localStorage.setItem('lyftr_session_id', 's-new-404');
    vi.spyOn(client, 'getSessionMessages').mockRejectedValue({
      response: { status: 404 },
    });

    render(<App />);

    await waitFor(() => {
      // Welcome screen with prompt suggestions is shown when empty
      expect(screen.getByRole('heading', { name: 'Lyftr AI' })).toBeTruthy();
      expect(screen.getByText('What is the capital of France?')).toBeTruthy();
    });
  });

  it('handles backend error on restore gracefully without breaking UI', async () => {
    localStorage.setItem('lyftr_session_id', 's-error');
    vi.spyOn(client, 'getSessionMessages').mockRejectedValue(new Error('Network error'));

    render(<App />);

    await waitFor(() => {
      expect(screen.getByText(/Could not restore previous conversation from server/i)).toBeTruthy();
    });
    // UI input remains usable
    const input = screen.getByTestId('chat-input') as HTMLTextAreaElement;
    expect(input.disabled).toBe(false);
  });

  it('new chat creates a fresh session ID and clears chat messages', async () => {
    localStorage.setItem('lyftr_session_id', 's-old1234');
    vi.spyOn(client, 'getSessionMessages').mockResolvedValue({
      session_id: 's-old1234',
      messages: [{ role: 'user', content: 'Old conversation text' }],
    });

    render(<App />);

    await waitFor(() => {
      expect(screen.getByText('Old conversation text')).toBeTruthy();
    });

    const newChatBtn = screen.getByRole('button', { name: /new chat/i });
    fireEvent.click(newChatBtn);

    const freshSession = localStorage.getItem('lyftr_session_id');
    expect(freshSession).not.toBe('s-old1234');
    expect(screen.queryByText('Old conversation text')).toBeNull();
  });

  it('discards late responses after new chat is clicked', async () => {
    let resolveSend: (val: any) => void;
    const sendPromise = new Promise((resolve) => {
      resolveSend = resolve;
    });

    vi.spyOn(client, 'getSessionMessages').mockResolvedValue({
      session_id: 's-wait',
      messages: [],
    });
    vi.spyOn(client, 'sendMessage').mockReturnValue(sendPromise as any);

    render(<App />);

    const input = screen.getByTestId('chat-input');
    fireEvent.change(input, { target: { value: 'Question for session A' } });
    const sendBtn = screen.getByTestId('send-button');
    fireEvent.click(sendBtn);

    // Click New Chat before response arrives
    const newChatBtn = screen.getByRole('button', { name: /new chat/i });
    fireEvent.click(newChatBtn);

    // Now resolve late reply from session A
    await act(async () => {
      resolveSend!({
        session_id: 's-wait',
        reply: 'Late reply from session A',
        metadata: {
          resolution_source: 'LIVE_INFERENCE',
          model: 'gemini/gemini-2.0-flash',
          latency_ms: 500,
          input_tokens: 10,
          output_tokens: 5,
          key_id: 'key_1',
          fallback_attempts: 0,
        },
      });
    });

    await waitFor(() => {
      expect(screen.queryByText('Late reply from session A')).toBeNull();
    });
  });

  it('prevents double-submit while a request is in flight', async () => {
    let resolveCall: any;
    const pendingPromise = new Promise((resolve) => {
      resolveCall = resolve;
    });
    vi.spyOn(client, 'getSessionMessages').mockResolvedValue({
      session_id: 's-double',
      messages: [],
    });
    const sendSpy = vi.spyOn(client, 'sendMessage').mockReturnValue(pendingPromise as any);

    render(<App />);

    const input = screen.getByTestId('chat-input');
    fireEvent.change(input, { target: { value: 'Single submit check' } });
    const sendBtn = screen.getByTestId('send-button');

    // Click multiple times rapidly
    fireEvent.click(sendBtn);
    fireEvent.click(sendBtn);
    fireEvent.click(sendBtn);

    expect(sendSpy).toHaveBeenCalledTimes(1);

    await act(async () => {
      resolveCall({
        session_id: 's-double',
        reply: 'Answer',
        metadata: {
          resolution_source: 'LIVE_INFERENCE',
          model: 'gemini/gemini-2.0-flash',
          latency_ms: 200,
          input_tokens: 5,
          output_tokens: 5,
          key_id: 'key_1',
          fallback_attempts: 0,
        },
      });
    });
  });
});

describe('API Client Error Mapping', () => {
  it('maps 503 to unavailable message', () => {
    const err = { response: { status: 503 } };
    expect(client.getErrorMessage(err)).toContain('All AI providers are currently unavailable');
  });

  it('maps 422 with detail list to readable comma-separated string', () => {
    const err = {
      response: {
        status: 422,
        data: {
          detail: [{ msg: 'session_id too long' }, { msg: 'message required' }],
        },
      },
    };
    expect(client.getErrorMessage(err)).toBe('session_id too long, message required');
  });

  it('maps timeout ECONNABORTED', () => {
    const err = { code: 'ECONNABORTED' };
    expect(client.getErrorMessage(err)).toContain('Request timed out');
  });

  it('maps missing response to connection error', () => {
    const err = { response: undefined, isAxiosError: true };
    expect(client.getErrorMessage(err)).toContain('Cannot connect to the backend');
  });
});
