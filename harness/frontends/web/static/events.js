/**
 * Harness-Zero SSE Event Stream Client (ES Module)
 * Handles auto-reconnect, Last-Event-ID tracking, and typed event dispatching (§11).
 */

export class HarnessEventStream {
  constructor(api, options = {}) {
    this.api = api;
    this.options = options;
    this.eventSource = null;
    this.lastEventId = null;
    this.listeners = new Map();
    this.anyListeners = new Set();
    this.reconnectAttempts = 0;
    this.reconnectTimer = null;
    this.isConnected = false;
    this.isManualDisconnect = false;
  }

  async connect() {
    this.isManualDisconnect = false;
    if (this.eventSource) {
      this.disconnect();
    }

    let url = `${this.api.baseUrl}/events`;
    const params = new URLSearchParams();
    if (this.lastEventId) {
      params.set('last_event_id', this.lastEventId);
    }

    // Exchange token for a single-use short-lived SSE ticket (§2.2, §4.3 #5)
    // or rely on the HttpOnly harness_token cookie set on page load.
    if (this.api && typeof this.api.getAuthTicket === 'function') {
      try {
        const ticketData = await this.api.getAuthTicket();
        if (ticketData && ticketData.ticket) {
          params.set('ticket', ticketData.ticket);
        }
      } catch (err) {
        // Fallback to cookie authentication
      }
    }

    const query = params.toString();
    if (query) {
      url += `?${query}`;
    }

    this._dispatchInternal('connecting', {});

    try {
      this.eventSource = new EventSource(url);

      this.eventSource.onopen = () => {
        this.isConnected = true;
        this.reconnectAttempts = 0;
        this._dispatchInternal('connected', {});
      };

      this.eventSource.onmessage = (e) => {
        if (!e.data || e.data.trim() === '') return;
        try {
          const envelope = JSON.parse(e.data);
          if (e.lastEventId) {
            this.lastEventId = e.lastEventId;
          } else if (envelope.id) {
            this.lastEventId = envelope.id;
          }
          this._dispatch(envelope.type || 'message', envelope);
        } catch (err) {
          console.warn('[HarnessEventStream] Failed to parse SSE payload:', err, e.data);
        }
      };

      this.eventSource.onerror = (e) => {
        this.isConnected = false;
        this._dispatchInternal('disconnected', { error: e });
        if (this.eventSource) {
          this.eventSource.close();
          this.eventSource = null;
        }

        if (!this.isManualDisconnect) {
          this._scheduleReconnect();
        }
      };
    } catch (err) {
      console.error('[HarnessEventStream] Error creating EventSource:', err);
      this._scheduleReconnect();
    }
  }

  _scheduleReconnect() {
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.reconnectAttempts++;
    const delay = Math.min(1000 * Math.pow(1.5, this.reconnectAttempts - 1), 10000);
    this._dispatchInternal('reconnecting', { attempt: this.reconnectAttempts, delay });

    this.reconnectTimer = setTimeout(() => {
      this.connect();
    }, delay);
  }

  disconnect() {
    this.isManualDisconnect = true;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.eventSource) {
      this.eventSource.close();
      this.eventSource = null;
    }
    this.isConnected = false;
    this._dispatchInternal('disconnected', {});
  }

  on(eventType, callback) {
    if (!this.listeners.has(eventType)) {
      this.listeners.set(eventType, new Set());
    }
    this.listeners.get(eventType).add(callback);
    return () => this.off(eventType, callback);
  }

  off(eventType, callback) {
    const handlers = this.listeners.get(eventType);
    if (handlers) {
      handlers.delete(callback);
      if (handlers.size === 0) {
        this.listeners.delete(eventType);
      }
    }
  }

  onAny(callback) {
    this.anyListeners.add(callback);
    return () => this.anyListeners.delete(callback);
  }

  _dispatch(type, envelope) {
    const handlers = this.listeners.get(type);
    if (handlers) {
      for (const handler of handlers) {
        try {
          handler(envelope.data, envelope);
        } catch (err) {
          console.error(`[HarnessEventStream] Error in listener for '${type}':`, err);
        }
      }
    }
    for (const anyHandler of this.anyListeners) {
      try {
        anyHandler(type, envelope.data, envelope);
      } catch (err) {
        console.error(`[HarnessEventStream] Error in onAny listener for '${type}':`, err);
      }
    }
  }

  _dispatchInternal(lifecycleEvent, data) {
    const handlers = this.listeners.get(`$${lifecycleEvent}`);
    if (handlers) {
      for (const handler of handlers) {
        try {
          handler(data);
        } catch (err) {
          console.error(`[HarnessEventStream] Error in lifecycle listener '$${lifecycleEvent}':`, err);
        }
      }
    }
  }
}
