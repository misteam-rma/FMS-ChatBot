import React, { useState, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { FiSend, FiLogOut, FiCommand, FiMessageSquare, FiUser } from 'react-icons/fi';
import toast from 'react-hot-toast';
import axios from 'axios';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

export default function DashboardPage() {
  const [userInfo, setUserInfo] = useState(null);
  const [supportInfo, setSupportInfo] = useState(null);
  const [messages, setMessages] = useState([
    {
      id: 1,
      type: 'ai',
      text: '👋 Welcome! I\'m your personal assistant. I can help you with your profile information and details. What would you like to know?',
      timestamp: new Date().toISOString()
    }
  ]);
  const [inputText, setInputText] = useState('');
  const [isTyping, setIsTyping] = useState(false);
  const messagesEndRef = useRef(null);
  const navigate = useNavigate();

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages, isTyping]);

  useEffect(() => {
    const storedInfo = localStorage.getItem('user_info');
    if (!storedInfo) {
      navigate('/login');
      return;
    }
    const parsed = JSON.parse(storedInfo);
    setUserInfo(parsed);

    setMessages([
      {
        id: 1,
        type: 'ai',
        text: `👋 Welcome, ${parsed.employee_name.split(' ')[0]}! I'm your personal assistant. I can help you with your profile information, personal details, and more. What would you like to know?`,
        timestamp: new Date().toISOString()
      }
    ]);
  }, [navigate]);

  const handleSend = async () => {
    if (!inputText.trim()) return;

    const newMsg = {
      id: Date.now(),
      type: 'human',
      text: inputText,
      timestamp: new Date().toISOString()
    };

    setMessages(prev => [...prev, newMsg]);
    setInputText('');
    setIsTyping(true);

    try {
      const token = localStorage.getItem('auth_token');
      console.log(`[FRONTEND LOG] 👉 Sending Chat Message: "${inputText}"`);
      const response = await axios.post(`${import.meta.env.VITE_API_URL || 'http://localhost:8000'}/api/chat/send`,
        { message: inputText },
        { headers: { Authorization: `Bearer ${token}` } }
      );

      console.log(`[FRONTEND LOG] ✅ Received Reply:`, response.data);
      setMessages(prev => [
        ...prev,
        {
          id: Date.now() + 1,
          type: 'ai',
          text: response.data.reply,
          timestamp: new Date().toISOString()
        }
      ]);
    } catch (error) {
      console.error("[FRONTEND ERROR] ❌ Chat failed:", error);
      setMessages(prev => [
        ...prev,
        {
          id: Date.now() + 1,
          type: 'ai',
          text: "I'm sorry, I'm having trouble connecting to the system right now. Please try again.",
          timestamp: new Date().toISOString()
        }
      ]);
    } finally {
      setIsTyping(false);
    }
  };

  const handleLogout = () => {
    localStorage.removeItem('user_info');
    localStorage.removeItem('auth_token');
    toast('Logged out successfully', { icon: '👋' });
    navigate('/login');
  };

  return (
    <div className="dashboard-layout fade-in">
      {/* Sidebar */}
      <div className="sidebar">
        <div className="sidebar-header">
          <FiUser style={{ marginRight: '0.75rem', color: 'var(--accent-color)', fontSize: '1.25rem' }} />
          <span>My Portal</span>
        </div>

        <div className="sidebar-content" style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
          <div className="nav-menu glass" style={{ marginBottom: '1rem', padding: '0.5rem', display: 'flex', flexDirection: 'column', gap: '0.25rem' }}>
            <button
              disabled
              className="btn btn-icon"
              style={{ width: '100%', justifyContent: 'flex-start', padding: '0.75rem', gap: '0.75rem', fontSize: '0.9rem', backgroundColor: 'var(--brand-primary)', color: 'white', border: 'none', textAlign: 'left' }}
            >
              <FiMessageSquare /> My Assistant
            </button>
          </div>

          <div className="support-card glass" style={{ marginBottom: '1rem', padding: '1rem' }}>
            <h4 style={{ fontSize: '0.8rem', textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--text-tertiary)', marginBottom: '0.75rem' }}>Need Help?</h4>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
              <p style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', margin: 0 }}>
                Ask me about your details, leave balance, or company info
              </p>
            </div>
          </div>

          <div style={{ flex: 1 }}></div>
        </div>

        <div className="sidebar-footer">
          <div className="avatar" style={{ background: 'var(--accent-color)' }}>{userInfo?.employee_name?.charAt(0) || 'U'}</div>
          <div className="user-info">
            <div className="user-name">{userInfo?.employee_name || 'User'}</div>
            <div className="user-role">{userInfo?.employee_id}</div>
          </div>
          <button onClick={handleLogout} className="btn btn-icon" title="Logout" style={{ color: 'var(--text-tertiary)' }}>
            <FiLogOut size={18} />
          </button>
        </div>
      </div>

      {/* Chat Area */}
      <div className="chat-area">
        <div className="chat-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div className="chat-title" style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            Your Personal Assistant
          </div>

          <div className="header-actions" style={{ paddingRight: '3.5rem' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
              <div style={{ width: '8px', height: '8px', borderRadius: '50%', backgroundColor: 'var(--success)' }}></div>
              <span style={{ color: 'var(--text-secondary)', fontSize: '0.85rem', fontWeight: 500 }}>Live Agent</span>
            </div>
          </div>
        </div>

        <div className="chat-messages">
          {messages.map((msg) => (
            <div key={msg.id} className={`message ${msg.type}`}>
              {msg.type === 'ai' && (
                <div className="message-avatar" style={{ background: 'var(--brand-primary)', color: 'white' }}>
                  <FiCommand size={14} />
                </div>
              )}
              <div className="message-content">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.text}</ReactMarkdown>
              </div>
              {msg.type === 'human' && (
                <div className="message-avatar" style={{ background: 'var(--accent-color)', color: 'white' }}>
                  <FiUser size={14} />
                </div>
              )}
            </div>
          ))}
          {isTyping && (
            <div className="message ai">
              <div className="message-avatar" style={{ background: 'var(--brand-primary)', color: 'white' }}>
                <FiCommand size={14} />
              </div>
              <div className="message-content" style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', padding: '0.8rem 1rem' }}>
                <span style={{ fontSize: '0.9rem', color: 'var(--text-tertiary)', fontStyle: 'italic', fontWeight: 500 }}>Thinking</span>
                <div className="dot-typing" style={{ margin: '0 20px 0 4px' }}></div>
              </div>
            </div>
          )}
          <div ref={messagesEndRef} />
        </div>

        <div className="chat-input-area">
          <div className="chat-input-wrapper glass">
            <textarea
              className="chat-textarea"
              placeholder="Ask me about your profile, details, or anything else..."
              rows={1}
              value={inputText}
              onChange={(e) => setInputText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault();
                  handleSend();
                }
              }}
            />
            <button
              className="send-btn"
              onClick={handleSend}
              disabled={!inputText.trim()}
              style={{ background: inputText.trim() ? 'var(--accent-color)' : 'var(--text-tertiary)' }}
            >
              <FiSend size={18} />
            </button>
          </div>
        </div>
      </div>

      <style>{`
        .message-content p { margin: 0 0 0.5rem 0; }
        .message-content p:last-child { margin-bottom: 0; }
        .message-content ul, .message-content ol { margin: 0.5rem 0; padding-left: 1.5rem; }
        .message-content li { margin-bottom: 0.25rem; }
        .message-content strong { color: inherit; font-weight: 700; }
        .message-content h1, .message-content h2, .message-content h3 { margin: 1rem 0 0.5rem 0; font-size: 1.1rem; }
        .dot-typing {
          width: 4px; height: 4px; border-radius: 50%;
          background-color: var(--text-tertiary);
          box-shadow: 10px 0 0 0 var(--text-tertiary), 20px 0 0 0 var(--text-tertiary);
          animation: dot-typing 1s infinite alternate;
        }
        @keyframes dot-typing {
          0% { opacity: 0.2; }
          100% { opacity: 1; }
        }
      `}</style>
    </div>
  );
}
