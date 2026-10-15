/**
 * Harness-Zero API Client (ES Module)
 * Handles token storage, Bearer headers, and Command RPC dispatching (§11).
 */

export class HarnessAPI {
  constructor(options = {}) {
    this.baseUrl = options.baseUrl || '';
    this._token = options.token || this._resolveToken();
  }

  _resolveToken() {
    // 1. Meta tag token injected into index.html
    if (typeof document !== 'undefined') {
      const meta = document.querySelector('meta[name="harness-token"]');
      if (meta && meta.content && meta.content !== '__HARNESS_TOKEN__') {
        return meta.content;
      }
    }

    // 2. Query parameter token (e.g. from studio launcher or login link)
    if (typeof window !== 'undefined' && window.location) {
      const urlParams = new URLSearchParams(window.location.search);
      const queryToken = urlParams.get('token');
      if (queryToken) {
        localStorage.setItem('harness_auth_token', queryToken);
        // Clean URL to avoid leaking token in browser address bar / history
        const cleanUrl = window.location.pathname + window.location.hash;
        window.history.replaceState({}, document.title, cleanUrl);
        return queryToken;
      }
    }

    // 2. Global bootstrap token if injected
    if (typeof window !== 'undefined' && window.__HARNESS_TOKEN__) {
      return window.__HARNESS_TOKEN__;
    }

    // 3. Local storage token
    if (typeof localStorage !== 'undefined') {
      return localStorage.getItem('harness_auth_token') || '';
    }

    return '';
  }

  get token() {
    return this._token;
  }

  setToken(token) {
    this._token = token;
    if (typeof localStorage !== 'undefined') {
      localStorage.setItem('harness_auth_token', token);
    }
  }

  _headers(extra = {}) {
    const headers = {
      'Content-Type': 'application/json',
      ...extra,
    };
    if (this._token) {
      headers['Authorization'] = `Bearer ${this._token}`;
      headers['X-Harness-Token'] = this._token;
    }
    return headers;
  }

  async command(name, args = {}) {
    const res = await fetch(`${this.baseUrl}/commands/${encodeURIComponent(name)}`, {
      method: 'POST',
      headers: this._headers(),
      body: JSON.stringify(args),
    });

    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const err = data.error || `HTTP ${res.status}: ${res.statusText}`;
      throw new Error(err);
    }
    return data.result !== undefined ? data.result : data;
  }

  async health() {
    const res = await fetch(`${this.baseUrl}/health`, {
      headers: this._headers(),
    });
    if (!res.ok) {
      throw new Error(`Health check failed: HTTP ${res.status}`);
    }
    return await res.json();
  }

  async getAuthTicket() {
    const res = await fetch(`${this.baseUrl}/auth/ticket`, {
      method: 'POST',
      headers: this._headers(),
      body: JSON.stringify({}),
    });
    if (!res.ok) {
      return null;
    }
    return await res.json();
  }

  async submitTurn(prompt, options = {}) {
    return this.command('submit_turn', { prompt, ...options });
  }

  async cancelTurn(sessionId = null) {
    return this.command('cancel_turn', { session_id: sessionId });
  }

  async resolveApproval(requestId, decision, feedback = '') {
    return this.command('resolve_approval', {
      request_id: requestId,
      decision,
      feedback,
    });
  }

  async getSessions() {
    return this.command('sessions.list');
  }

  async switchSession(sessionId) {
    return this.command('sessions.switch', { session_id: sessionId });
  }

  async createSession(metadata = {}) {
    return this.command('sessions.create', { metadata });
  }

  async getSettings(path, defaultValue = null) {
    return this.command('settings.get', { path, default: defaultValue });
  }

  async setSettings(path, value) {
    return this.command('settings.set', { path, value });
  }

  async listModels() {
    return this.command('models.list');
  }

  async economics(sessionId = null) {
    return this.command('economics', { session_id: sessionId });
  }
}

export const api = new HarnessAPI();
