import { useState, useCallback, useEffect, useRef } from 'react';
import { v4 as uuidv4 } from 'uuid';
import { Message } from './types';
import { Sidebar } from './components/Sidebar';
import { ChatWindow } from './components/ChatWindow';
import { MessageInput } from './components/MessageInput';
import { ErrorNotification } from './components/ErrorNotification';
import { sendMessage, getSessionMessages, getErrorMessage } from './api/client';
import { Menu, X } from 'lucide-react';

function generateSessionId(): string {
  return `s-${uuidv4().slice(0, 8)}`;
}

function getStoredSessionId(): string {
  try {
    const stored = localStorage.getItem('lyftr_session_id');
    if (stored && /^[a-zA-Z0-9_-]+$/.test(stored)) {
      return stored;
    }
  } catch {
    // LocalStorage unavailable
  }
  const freshId = generateSessionId();
  try {
    localStorage.setItem('lyftr_session_id', freshId);
  } catch {
    // ignore
  }
  return freshId;
}

function setStoredSessionId(id: string): void {
  try {
    localStorage.setItem('lyftr_session_id', id);
  } catch {
    // ignore
  }
}

function getStoredPrompt(sessionId: string): string {
  try {
    return localStorage.getItem(`lyftr_prompt_${sessionId}`) || '';
  } catch {
    return '';
  }
}

function setStoredPrompt(sessionId: string, prompt: string): void {
  try {
    localStorage.setItem(`lyftr_prompt_${sessionId}`, prompt);
  } catch {
    // ignore
  }
}

function App() {
  const [sessionId, setSessionId] = useState<string>(() => getStoredSessionId());
  const [messages, setMessages] = useState<Message[]>([]);
  const [inputValue, setInputValue] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [systemPrompt, setSystemPrompt] = useState<string>(() => getStoredPrompt(sessionId));
  
  // Mobile sidebar: on mobile (<768px), default to closed so it does not overlay content
  const [isSidebarOpen, setIsSidebarOpen] = useState(() => {
    if (typeof window !== 'undefined') {
      return window.innerWidth >= 768;
    }
    return false;
  });

  const activeSessionIdRef = useRef<string>(sessionId);
  activeSessionIdRef.current = sessionId;

  const inFlightAbortRef = useRef<AbortController | null>(null);

  // Restore session history on mount
  useEffect(() => {
    let isCancelled = false;
    const targetSession = sessionId;

    async function restoreHistory() {
      try {
        const data = await getSessionMessages(targetSession);
        if (isCancelled || activeSessionIdRef.current !== targetSession) return;

        const restored: Message[] = data.messages.map((m, idx) => ({
          id: `restored-${idx}-${uuidv4().slice(0, 4)}`,
          role: m.role as 'user' | 'assistant',
          content: m.content,
          metadata: m.metadata,
          timestamp: new Date(),
        }));
        setMessages(restored);
      } catch (err: any) {
        if (isCancelled || activeSessionIdRef.current !== targetSession) return;
        // 404 means fresh session without prior history – perfectly normal
        if (err?.response?.status === 404) {
          setMessages([]);
        } else {
          setError('Could not restore previous conversation from server.');
        }
      }
    }

    restoreHistory();

    return () => {
      isCancelled = true;
    };
  }, [sessionId]);

  const handleSystemPromptChange = useCallback((prompt: string) => {
    setSystemPrompt(prompt);
    setStoredPrompt(sessionId, prompt);
  }, [sessionId]);

  const handleNewChat = useCallback(() => {
    // Abort any ongoing in-flight request
    if (inFlightAbortRef.current) {
      inFlightAbortRef.current.abort();
      inFlightAbortRef.current = null;
    }

    const freshId = generateSessionId();
    setStoredSessionId(freshId);
    setSessionId(freshId);
    activeSessionIdRef.current = freshId;

    const freshPrompt = getStoredPrompt(freshId);
    setSystemPrompt(freshPrompt);

    setMessages([]);
    setInputValue('');
    setError(null);
    setIsLoading(false);
  }, []);

  const handleSend = useCallback(async (message: string) => {
    const trimmed = message.trim();
    if (!trimmed || isLoading) return;

    const targetSessionId = sessionId;

    const userMessage: Message = {
      id: uuidv4(),
      role: 'user',
      content: trimmed,
      timestamp: new Date(),
    };

    const loadingMessageId = `loading-${uuidv4()}`;
    const loadingMessage: Message = {
      id: loadingMessageId,
      role: 'assistant',
      content: '',
      timestamp: new Date(),
      isLoading: true,
    };

    setMessages((prev) => [...prev, userMessage, loadingMessage]);
    setInputValue('');
    setIsLoading(true);
    setError(null);

    const abortController = new AbortController();
    inFlightAbortRef.current = abortController;

    try {
      const response = await sendMessage(
        {
          session_id: targetSessionId,
          message: trimmed,
          system_prompt: systemPrompt || undefined,
        },
        undefined,
        abortController.signal
      );

      // Guard: if user clicked New Chat while waiting, do not write to the new session
      if (activeSessionIdRef.current !== targetSessionId) {
        return;
      }

      const assistantMessage: Message = {
        id: uuidv4(),
        role: 'assistant',
        content: response.reply,
        metadata: response.metadata,
        timestamp: new Date(),
      };

      setMessages((prev) => [
        ...prev.filter((m) => m.id !== loadingMessageId && !m.isLoading),
        assistantMessage,
      ]);
    } catch (err: any) {
      if (activeSessionIdRef.current !== targetSessionId) {
        return;
      }
      if (err?.name === 'CanceledError' || err?.code === 'ERR_CANCELED') {
        // Request was aborted cleanly on new chat
        return;
      }

      const errorMsg = getErrorMessage(err);
      setError(errorMsg);

      const errorMessage: Message = {
        id: uuidv4(),
        role: 'assistant',
        content: `❌ ${errorMsg}`,
        timestamp: new Date(),
      };

      setMessages((prev) => [
        ...prev.filter((m) => m.id !== loadingMessageId && !m.isLoading),
        errorMessage,
      ]);
    } finally {
      if (activeSessionIdRef.current === targetSessionId) {
        setIsLoading(false);
      }
      if (inFlightAbortRef.current === abortController) {
        inFlightAbortRef.current = null;
      }
    }
  }, [sessionId, systemPrompt, isLoading]);

  const handleSuggestedPrompt = useCallback((prompt: string) => {
    setInputValue(prompt);
  }, []);

  return (
    <div className="flex h-screen bg-slate-900 overflow-hidden">
      {/* Mobile sidebar overlay */}
      {isSidebarOpen && (
        <div
          className="fixed inset-0 bg-black/50 z-10 md:hidden"
          onClick={() => setIsSidebarOpen(false)}
        />
      )}

      {/* Sidebar */}
      <div
        className={`${
          isSidebarOpen ? 'translate-x-0' : '-translate-x-full'
        } fixed md:relative md:translate-x-0 z-20 md:z-auto transition-transform duration-300 h-full`}
      >
        <Sidebar
          sessionId={sessionId}
          systemPrompt={systemPrompt}
          onSystemPromptChange={handleSystemPromptChange}
          onNewChat={handleNewChat}
        />
      </div>

      {/* Main content */}
      <div className="flex flex-col flex-1 min-w-0">
        {/* Mobile header */}
        <div className="flex items-center gap-3 p-3 border-b border-slate-800 md:hidden">
          <button
            onClick={() => setIsSidebarOpen((prev) => !prev)}
            className="p-2 text-slate-400 hover:text-slate-200 hover:bg-slate-800 rounded-lg transition-colors"
            aria-label="Toggle navigation menu"
          >
            {isSidebarOpen ? <X className="w-5 h-5" /> : <Menu className="w-5 h-5" />}
          </button>
          <span className="text-white font-semibold">Lyftr AI</span>
        </div>

        {/* Chat area */}
        <ChatWindow
          messages={messages}
          onSuggestedPrompt={handleSuggestedPrompt}
        />

        {/* Input */}
        <MessageInput
          value={inputValue}
          onChange={setInputValue}
          onSend={handleSend}
          isLoading={isLoading}
        />
      </div>

      {/* Error notification */}
      {error && (
        <ErrorNotification
          message={error}
          onDismiss={() => setError(null)}
        />
      )}
    </div>
  );
}

export default App;
