import { useState, useRef, useEffect } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { queryVideo, getConversationMessages } from '../api/videoApi';

function ChatInterface({ userId, sessionId }) {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const messagesEndRef = useRef(null);

  // Get or create conversationId for this session (persisted in localStorage)
  const getConversationId = () => {
    const storageKey = `conversation_${userId}_${sessionId}`;
    let convId = localStorage.getItem(storageKey);
    if (!convId) {
      convId = 'conv_' + Math.random().toString(36).substr(2, 9);
      localStorage.setItem(storageKey, convId);
    }
    return convId;
  };

  const [conversationId, setConversationId] = useState(() => getConversationId());

  // Load conversation history when session changes
  useEffect(() => {
    const newConvId = getConversationId();
    setConversationId(newConvId);
    loadConversationHistory(newConvId);
  }, [sessionId, userId]);

  const loadConversationHistory = async (convId) => {
    setLoadingHistory(true);
    try {
      const data = await getConversationMessages(userId, convId);
      if (data.messages && data.messages.length > 0) {
        setMessages(data.messages);
      } else {
        setMessages([]);
      }
    } catch (err) {
      console.error('Failed to load conversation history:', err);
      setMessages([]);
    } finally {
      setLoadingHistory(false);
    }
  };

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!input.trim() || loading) return;

    const userMessage = input.trim();
    setInput('');
    setMessages((prev) => [...prev, { role: 'user', content: userMessage }]);
    setLoading(true);

    try {
      const result = await queryVideo(userId, sessionId, conversationId, userMessage);

      let responseText = result.response || '';

      // Add search results if present
      if (result.results && result.results.length > 0) {
        responseText += '\n\n**Relevant segments:**\n';
        result.results.forEach((r, i) => {
          responseText += `\n${i + 1}. \`${formatTime(r.start)}\` - ${r.text}`;
        });
      }

      // Add key points if present
      if (result.key_points && result.key_points.length > 0) {
        responseText += '\n\n**Key points:**\n';
        result.key_points.forEach((kp, i) => {
          responseText += `\n${i + 1}. **${kp.title}**: ${kp.summary}`;
        });
      }

      setMessages((prev) => [...prev, { role: 'assistant', content: responseText }]);
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        { role: 'assistant', content: `**Error:** ${err.response?.data?.detail || 'Failed to get response'}` },
      ]);
    } finally {
      setLoading(false);
    }
  };

  const startNewConversation = () => {
    const storageKey = `conversation_${userId}_${sessionId}`;
    const newConvId = 'conv_' + Math.random().toString(36).substr(2, 9);
    localStorage.setItem(storageKey, newConvId);
    setConversationId(newConvId);
    setMessages([]);
  };

  return (
    <div className="chat-container">
      <div className="chat-header">
        <span>
          {messages.length > 0 ? `${messages.length} messages` : 'New conversation'}
        </span>
        {messages.length > 0 && (
          <button
            onClick={startNewConversation}
            className="btn btn-secondary btn-sm"
          >
            New Chat
          </button>
        )}
      </div>
      <div className="chat-messages">
        {loadingHistory ? (
          <div className="loading">
            <div className="loading-spinner" style={{ width: 32, height: 32 }}></div>
            <span className="loading-text">Loading conversation...</span>
          </div>
        ) : messages.length === 0 ? (
          <div className="empty-state">
            <p>Ask questions about the video</p>
            <p style={{ fontSize: 12, marginTop: 10 }}>
              Try: "What is this video about?" or "Summarize the main points"
            </p>
          </div>
        ) : null}
        {messages.map((msg, i) => (
          <div key={i} className={`message ${msg.role}`}>
            <div className="message-content markdown-content">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {msg.content}
              </ReactMarkdown>
            </div>
          </div>
        ))}
        {loading && (
          <div className="message assistant">
            <div className="typing-indicator">
              <span></span>
              <span></span>
              <span></span>
            </div>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      <form onSubmit={handleSubmit} className="chat-input">
        <input
          type="text"
          placeholder="Ask a question about the video..."
          value={input}
          onChange={(e) => setInput(e.target.value)}
          disabled={loading}
        />
        <button type="submit" className="btn btn-primary" disabled={loading || !input.trim()}>
          Send
        </button>
      </form>
    </div>
  );
}

function formatTime(seconds) {
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return `${mins}:${secs.toString().padStart(2, '0')}`;
}

export default ChatInterface;
