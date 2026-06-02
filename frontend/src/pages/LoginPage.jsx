import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import toast from 'react-hot-toast';
import { FiPhone } from 'react-icons/fi';
import axios from 'axios';

export default function LoginPage() {
  const [mobileNumber, setMobileNumber] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const navigate = useNavigate();

  const handleSubmit = async (e) => {
    e.preventDefault();
    setIsLoading(true);

    console.log("[FRONTEND LOG] 🚀 Starting Mobile Verification...");
    const payload = {
      mobile_number: mobileNumber.trim()
    };
    console.log("[FRONTEND LOG] 📦 Payload:", payload);

    try {
      console.log(`[FRONTEND LOG] 🌐 Sending POST to verify mobile number`);
      const response = await axios.post(`${import.meta.env.VITE_API_URL || 'http://localhost:8000'}/api/auth/verify-mobile`, payload);

      console.log("[FRONTEND LOG] ✅ Backend Response received:", response.status, response.data);

      if (response.data && response.data.access_token) {
        localStorage.setItem('auth_token', response.data.access_token);
        localStorage.setItem('user_info', JSON.stringify(response.data));
        toast.success('Access granted!');
        navigate('/dashboard');
      } else {
        console.error("[FRONTEND ERROR] ❌ Unexpected response format:", response.data);
        toast.error('Unexpected response format.');
      }
    } catch (error) {
      console.error("[FRONTEND ERROR] 🔥 Verification failed:", error);
      if (error.response) {
        console.error("[FRONTEND ERROR] ⚠️ Server Error:", error.response.status);
        if (error.response.data && error.response.data.detail) {
          toast.error(error.response.data.detail);
        } else {
          toast.error(`Server Error: ${error.response.status}`);
        }
      } else if (error.request) {
        console.error("[FRONTEND ERROR] 🔌 Network Error:", error.request);
        toast.error('Could not reach the backend. Check if the server is running.');
      } else {
        console.error("[FRONTEND ERROR] ⚙️ Setup Error:", error.message);
        toast.error(error.message || 'Verification failed. Please try again.');
      }
    } finally {
      setIsLoading(false);
      console.log("[FRONTEND LOG] 🏁 Verification process finished.");
    }
  };

  return (
    <div className="auth-layout fade-in">
      <div className="auth-wrapper">
        <div className="auth-card">
          <h1>HR Support</h1>
          <p className="subtitle">Verify your mobile number to access your information</p>

          <form className="auth-form" onSubmit={handleSubmit}>
            <div className="form-group">
              <label>Mobile Number</label>
              <div style={{ position: 'relative' }}>
                <FiPhone style={{ position: 'absolute', left: '12px', top: '12px', color: 'var(--text-secondary)' }} />
                <input
                  type="tel"
                  className="input-field"
                  style={{ paddingLeft: '2.5rem' }}
                  placeholder="Enter your mobile number"
                  value={mobileNumber}
                  onChange={(e) => setMobileNumber(e.target.value)}
                  required
                />
              </div>
            </div>

            <div className="form-actions">
              <button type="submit" className="btn btn-primary btn-full" disabled={isLoading}>
                {isLoading ? 'Verifying...' : 'Verify'}
              </button>
            </div>
          </form>
        </div>
      </div>
    </div>
  );
}
