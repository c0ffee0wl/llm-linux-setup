/**
 * llm-guiassistant - WebSocket-based conversation UI
 *
 * Handles:
 * - WebSocket connection with auto-reconnect
 * - Streaming LLM responses
 * - Message editing, regeneration, branching
 * - Action panel (Ctrl+K)
 * - Input handling with readline shortcuts
 */

// Check if required libraries loaded
if (typeof marked === 'undefined' || typeof hljs === 'undefined' || typeof DOMPurify === 'undefined') {
    document.body.innerHTML = '<div style="padding: 20px; color: #c00;">' +
        '<h3>JavaScript assets failed to load</h3>' +
        '<p>Missing: ' +
        (typeof marked === 'undefined' ? 'marked.js ' : '') +
        (typeof hljs === 'undefined' ? 'highlight.js ' : '') +
        (typeof DOMPurify === 'undefined' ? 'purify.js' : '') +
        '</p>' +
        '<p>Run: <code>./install-llm-tools.sh</code> to download assets.</p>' +
        '</div>';
    throw new Error('Required JavaScript libraries not loaded');
}

// Helper function to safely render markdown with DOMPurify sanitization
function safeMarkdown(content) {
    return DOMPurify.sanitize(marked.parse(content), {
        ALLOWED_TAGS: ['p', 'br', 'strong', 'em', 'code', 'pre', 'ul', 'ol', 'li', 'a', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'blockquote', 'hr', 'table', 'thead', 'tbody', 'tr', 'th', 'td', 'span', 'div'],
        ALLOWED_ATTR: ['href', 'title', 'class', 'target', 'rel'],
        ALLOW_DATA_ATTR: false
    });
}

// Configure marked for safe rendering
marked.setOptions({
    breaks: true,
    gfm: true
});

// Security: Strip raw HTML to prevent XSS attacks from LLM responses
const renderer = new marked.Renderer();
renderer.html = function(token) {
    return '';
};
marked.use({ renderer });

// ============================================================================
// State
// ============================================================================

let ws = null;
let reconnectAttempts = 0;
let currentMessageId = null;
let sessionId = null;
let isStreaming = false;
let currentToolCallId = null;

// Pending requests for async responses (e.g., stripMarkdown)
const pendingRequests = new Map();

// Pending images for capture/upload
window.pendingImages = [];

// Message store for tracking conversation history
const messageStore = {
    messages: [],

    add(role, content, element) {
        const id = `msg-${Date.now()}-${this.messages.length}`;
        element.dataset.messageId = id;
        element.dataset.messageIndex = this.messages.length;
        this.messages.push({ id, role, content, element });
        return id;
    },

    getLastByRole(role) {
        for (let i = this.messages.length - 1; i >= 0; i--) {
            if (this.messages[i].role === role) return this.messages[i];
        }
        return null;
    },

    truncateAfter(id) {
        const idx = this.messages.findIndex(m => m.id === id);
        if (idx === -1) return;
        for (let i = idx + 1; i < this.messages.length; i++) {
            this.messages[i].element?.remove();
        }
        this.messages = this.messages.slice(0, idx + 1);
    },

    removeMessage(id) {
        const idx = this.messages.findIndex(m => m.id === id);
        if (idx === -1) return null;
        const msg = this.messages[idx];
        msg.element?.remove();
        this.messages.splice(idx, 1);
        this.messages.forEach((m, i) => {
            m.element.dataset.messageIndex = i;
        });
        return msg;
    },

    getMessagesUpTo(id) {
        const idx = this.messages.findIndex(m => m.id === id);
        return idx === -1 ? [] : this.messages.slice(0, idx + 1);
    },

    clear() {
        this.messages = [];
    }
};

// SVG Icons
const ICONS = {
    copy: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg>',
    copyPlain: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/><path d="M12 13h7M15.5 13v6" stroke-width="1.5"/></svg>',
    edit: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 013 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>',
    regenerate: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 11-2.12-9.36L23 10"/></svg>',
    branch: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="6" y1="3" x2="6" y2="15"/><circle cx="18" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M18 9a9 9 0 01-9 9"/></svg>',
    python: '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M12 0C5.4 0 5.8 2.8 5.8 2.8v2.9h6.3v.9H3.9S0 6.2 0 12.1s3.4 5.7 3.4 5.7h2v-2.8s-.1-3.4 3.3-3.4h5.7s3.2 0 3.2-3.1V3.4S18.1 0 12 0zm-3.1 2c.6 0 1 .4 1 1s-.4 1-1 1-1-.4-1-1 .4-1 1-1z"/><path d="M12 24c6.6 0 6.2-2.8 6.2-2.8v-2.9h-6.3v-.9h8.2s3.9.4 3.9-5.5-3.4-5.7-3.4-5.7h-2v2.8s.1 3.4-3.3 3.4H9.6s-3.2 0-3.2 3.1v5.1S5.9 24 12 24zm3.1-2c-.6 0-1-.4-1-1s.4-1 1-1 1 .4 1 1-.4 1-1 1z"/></svg>',
    shell: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg>',
    search: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>',
    web: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 014 10 15.3 15.3 0 01-4 10 15.3 15.3 0 01-4-10 15.3 15.3 0 014-10z"/></svg>',
    tool: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14.7 6.3a1 1 0 000 1.4l1.6 1.6a1 1 0 001.4 0l3.77-3.77a6 6 0 01-7.94 7.94l-6.91 6.91a2.12 2.12 0 01-3-3l6.91-6.91a6 6 0 017.94-7.94l-3.76 3.76z"/></svg>',
    check: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg>',
    chevron: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="9 18 15 12 9 6"/></svg>',
    thinking: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 015.83 1c0 2-3 3-3 3"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>'
};

// Tool display names and icons
const TOOL_CONFIG = {
    execute_python: { name: 'Python', icon: 'python' },
    sandboxed_shell: { name: 'Shell', icon: 'shell' },
    search_google: { name: 'Google Search', icon: 'search' },
    fetch_url: { name: 'Fetch URL', icon: 'web' },
    suggest_command: { name: 'Command', icon: 'shell' },
    capture_screen: { name: 'Screenshot', icon: 'tool' }
};

// ============================================================================
// History Sidebar
// ============================================================================

const historySidebar = {
    conversations: [],
    isLoading: false,

    async load() {
        if (this.isLoading) return;
        this.isLoading = true;

        try {
            const response = await fetch('/api/history');
            if (!response.ok) throw new Error('Failed to load history');
            const data = await response.json();
            // API returns grouped data: {"Today": [...], "Yesterday": [...], ...}
            this.groupedConversations = data;
            this.render();
        } catch (err) {
            console.error('History load error:', err);
            showToast('Failed to load history');
        } finally {
            this.isLoading = false;
        }
    },

    render() {
        const list = document.getElementById('history-list');
        if (!list) return;

        list.textContent = ''; // Clear safely

        // API returns pre-grouped data: {"Today": [...], "Yesterday": [...], ...}
        const groups = this.groupedConversations || {};
        const hasConversations = Object.values(groups).some(arr => arr.length > 0);

        if (!hasConversations) {
            const empty = document.createElement('div');
            empty.className = 'history-empty';
            empty.textContent = 'No conversations yet';
            list.appendChild(empty);
            return;
        }

        // Iterate in display order (also handles search results)
        const displayOrder = ['Search Results', 'Today', 'Yesterday', 'This Week', 'Older'];
        for (const label of displayOrder) {
            const items = groups[label];
            if (!items || items.length === 0) continue;

            const group = document.createElement('div');
            group.className = 'history-group';

            const header = document.createElement('div');
            header.className = 'history-group-header';
            header.textContent = label;
            group.appendChild(header);

            for (const conv of items) {
                const item = document.createElement('div');
                item.className = 'history-item';
                item.dataset.id = conv.id;
                item.onclick = () => this.loadConversation(conv.id);

                const itemHeader = document.createElement('div');
                itemHeader.className = 'history-item-header';

                const preview = document.createElement('span');
                preview.className = 'history-preview';
                preview.textContent = conv.preview || 'Empty conversation';
                itemHeader.appendChild(preview);

                // Source badge
                const badge = this.createSourceBadge(conv.source);
                if (badge) itemHeader.appendChild(badge);

                item.appendChild(itemHeader);

                const meta = document.createElement('div');
                meta.className = 'history-meta';
                meta.textContent = (conv.model || '') + ' · ' + (conv.message_count || 0) + ' msgs';
                item.appendChild(meta);

                group.appendChild(item);
            }

            list.appendChild(group);
        }
    },

    createSourceBadge(source) {
        const config = {
            gui: { text: 'G', title: 'GUI', cls: 'badge-gui' },
            tui: { text: 'T', title: 'TUI', cls: 'badge-tui' },
            inline: { text: 'I', title: 'Inline', cls: 'badge-inline' },
            cli: { text: 'C', title: 'CLI', cls: 'badge-cli' }
        };
        const cfg = config[source] || { text: '?', title: 'Unknown', cls: '' };
        const badge = document.createElement('span');
        badge.className = 'source-badge ' + cfg.cls;
        badge.textContent = cfg.text;
        badge.title = cfg.title;
        return badge;
    },

    async loadConversation(id) {
        try {
            const response = await fetch('/api/history/' + id);
            if (!response.ok) throw new Error('Failed to load conversation');
            const data = await response.json();

            // Load into main view
            loadHistory(data.messages || []);
            showToast('Loaded conversation');

            // On mobile, collapse sidebar after selection
            if (window.innerWidth < 768) {
                this.toggle();
            }
        } catch (err) {
            console.error('Load conversation error:', err);
            showToast('Failed to load conversation');
        }
    },

    async search(query) {
        if (!query.trim()) {
            await this.load();
            return;
        }

        try {
            const response = await fetch('/api/history/search?q=' + encodeURIComponent(query));
            if (!response.ok) throw new Error('Search failed');
            const data = await response.json();
            // Search API returns {results: [...]} - put all in "Search Results" group
            const results = data.results || [];
            this.groupedConversations = results.length > 0
                ? { 'Search Results': results }
                : {};
            this.render();
        } catch (err) {
            console.error('Search error:', err);
        }
    },

    toggle() {
        const sidebar = document.getElementById('history-sidebar');
        if (sidebar) {
            sidebar.classList.toggle('collapsed');
            // Save state
            localStorage.setItem('sidebar_collapsed', sidebar.classList.contains('collapsed'));
        }
    },

    init() {
        // Restore sidebar state
        const collapsed = localStorage.getItem('sidebar_collapsed') !== 'false';
        const sidebar = document.getElementById('history-sidebar');
        if (sidebar && collapsed) {
            sidebar.classList.add('collapsed');
        }

        // Load history
        this.load();
    }
};

// ============================================================================
// @ Autocomplete
// ============================================================================

const atAutocomplete = {
    visible: false,
    suggestions: [],
    selectedIndex: 0,
    triggerStart: -1,
    currentPrefix: '',
    cwd: null,  // Set from context when available

    async getSuggestions(prefix) {
        try {
            // Use cwd from context, or fall back to root (server will use home)
            const cwdParam = this.cwd || '/';
            const response = await fetch('/api/completions?prefix=' + encodeURIComponent(prefix) + '&cwd=' + encodeURIComponent(cwdParam));
            if (!response.ok) return [];
            const data = await response.json();
            return data.completions || [];
        } catch (err) {
            console.error('Completions error:', err);
            return [];
        }
    },

    render() {
        const dropdown = document.getElementById('at-autocomplete');
        const list = document.getElementById('at-autocomplete-list');
        if (!dropdown || !list) return;

        if (this.suggestions.length === 0) {
            this.hide();
            return;
        }

        list.textContent = ''; // Clear safely

        this.suggestions.forEach((item, i) => {
            const div = document.createElement('div');
            div.className = 'at-completion' + (i === this.selectedIndex ? ' selected' : '');
            div.dataset.index = i;

            const text = document.createElement('span');
            text.className = 'at-completion-text';
            text.textContent = item.text;
            div.appendChild(text);

            // Show type badge if available
            if (item.type) {
                const type = document.createElement('span');
                type.className = 'at-completion-type';
                type.textContent = item.type;
                div.appendChild(type);
            }

            const desc = document.createElement('span');
            desc.className = 'at-completion-desc';
            desc.textContent = item.description || '';
            div.appendChild(desc);

            div.onclick = () => this.select(i);
            list.appendChild(div);
        });

        dropdown.classList.remove('hidden');
        this.visible = true;

        // Position dropdown near input
        this.positionDropdown();
    },

    positionDropdown() {
        const dropdown = document.getElementById('at-autocomplete');
        const input = document.getElementById('input');
        if (!dropdown || !input) return;

        const rect = input.getBoundingClientRect();
        dropdown.style.bottom = (window.innerHeight - rect.top + 5) + 'px';
        dropdown.style.left = rect.left + 'px';
        dropdown.style.width = Math.min(rect.width, 400) + 'px';
    },

    select(index) {
        if (index < 0 || index >= this.suggestions.length) return;

        const item = this.suggestions[index];
        const input = document.getElementById('input');
        if (!input) return;

        // Replace @ prefix with selected item
        const before = input.value.substring(0, this.triggerStart);
        const after = input.value.substring(input.selectionStart);
        input.value = before + item.text + ' ' + after;
        input.setSelectionRange(before.length + item.text.length + 1, before.length + item.text.length + 1);

        this.hide();
        autoResizeInput();
    },

    hide() {
        const dropdown = document.getElementById('at-autocomplete');
        if (dropdown) {
            dropdown.classList.add('hidden');
        }
        this.visible = false;
        this.suggestions = [];
        this.selectedIndex = 0;
    },

    navigate(direction) {
        if (!this.visible) return;
        this.selectedIndex = Math.max(0, Math.min(this.suggestions.length - 1, this.selectedIndex + direction));
        this.render();
    },

    async handleInput(input) {
        const text = input.value;
        const pos = input.selectionStart;

        // Find @ before cursor
        let atPos = -1;
        for (let i = pos - 1; i >= 0; i--) {
            if (text[i] === '@') {
                atPos = i;
                break;
            }
            if (/\s/.test(text[i])) break;
        }

        if (atPos === -1) {
            this.hide();
            return;
        }

        const prefix = text.substring(atPos, pos);
        if (prefix === this.currentPrefix) return;

        this.currentPrefix = prefix;
        this.triggerStart = atPos;

        // Fetch suggestions
        this.suggestions = await this.getSuggestions(prefix);
        this.selectedIndex = 0;
        this.render();
    }
};

// ============================================================================
// Capture Controls
// ============================================================================

const captureControls = {
    delay: parseInt(localStorage.getItem('capture_delay') || '3', 10),
    dropdownVisible: false,

    init() {
        const delayInput = document.getElementById('capture-delay-input');
        const delaySlider = document.getElementById('capture-delay-slider');

        if (delayInput) {
            delayInput.value = this.delay;
            delayInput.addEventListener('input', (e) => {
                this.setDelay(parseInt(e.target.value, 10) || 0);
            });
        }

        if (delaySlider) {
            delaySlider.value = this.delay;
            delaySlider.addEventListener('input', (e) => {
                this.setDelay(parseInt(e.target.value, 10) || 0);
            });
        }

        this.updateLabel();
    },

    setDelay(value) {
        this.delay = Math.max(0, Math.min(60, value));
        localStorage.setItem('capture_delay', this.delay.toString());

        // Sync UI elements
        const delayInput = document.getElementById('capture-delay-input');
        const delaySlider = document.getElementById('capture-delay-slider');

        if (delayInput) delayInput.value = this.delay;
        if (delaySlider) delaySlider.value = this.delay;
        this.updateLabel();
    },

    updateLabel() {
        const label = document.getElementById('capture-delay-label');
        if (label) {
            label.textContent = this.delay + 's';
        }
    },

    toggleDropdown() {
        const dropdown = document.getElementById('capture-dropdown');
        if (dropdown) {
            dropdown.classList.toggle('hidden');
            this.dropdownVisible = !dropdown.classList.contains('hidden');
        }
    },

    hideDropdown() {
        const dropdown = document.getElementById('capture-dropdown');
        if (dropdown) {
            dropdown.classList.add('hidden');
            this.dropdownVisible = false;
        }
    },

    async capture(mode) {
        this.hideDropdown();

        try {
            showToast('Capturing ' + mode + (this.delay > 0 ? ' in ' + this.delay + 's...' : '...'));

            const response = await fetch('/api/capture', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ mode: mode, delay: this.delay })
            });

            if (!response.ok) {
                const err = await response.json();
                throw new Error(err.error || 'Capture failed');
            }

            const data = await response.json();
            if (data.path) {
                window.pendingImages.push(data.path);
                showToast('Screenshot captured');
            } else {
                showToast('Capture failed: no path returned');
            }
        } catch (err) {
            console.error('Capture error:', err);
            showToast('Capture failed: ' + err.message);
        }
    }
};

// ============================================================================
// RAG Panel
// ============================================================================

const ragPanel = {
    collections: [],
    activeCollection: null,

    async load() {
        try {
            const response = await fetch('/api/rag/collections');
            if (!response.ok) return;
            const data = await response.json();
            this.collections = data.collections || [];
            this.render();
        } catch (err) {
            console.error('RAG load error:', err);
        }
    },

    render() {
        const container = document.getElementById('rag-collections');
        if (!container) return;

        container.textContent = ''; // Clear safely

        if (this.collections.length === 0) {
            const empty = document.createElement('div');
            empty.className = 'rag-empty';
            empty.textContent = 'No collections. Add documents below.';
            container.appendChild(empty);
            return;
        }

        for (const coll of this.collections) {
            const div = document.createElement('div');
            div.className = 'rag-collection' + (coll.name === this.activeCollection ? ' active' : '');
            div.onclick = () => this.activate(coll.name);

            const name = document.createElement('span');
            name.className = 'rag-collection-name';
            name.textContent = coll.name;
            div.appendChild(name);

            const count = document.createElement('span');
            count.className = 'rag-collection-count';
            count.textContent = (coll.count || 0) + ' docs';
            div.appendChild(count);

            container.appendChild(div);
        }
    },

    async activate(name) {
        try {
            const response = await fetch('/api/rag/activate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ session: sessionId, collection: name })
            });

            if (!response.ok) throw new Error('Failed to activate collection');

            this.activeCollection = name;
            this.render();
            showToast('RAG: ' + name + ' activated');
        } catch (err) {
            console.error('RAG activate error:', err);
            showToast('Failed to activate collection');
        }
    },

    async addDocument(path) {
        if (!path.trim()) return;

        try {
            // Use default collection if none active
            const collection = this.activeCollection || 'default';

            const response = await fetch('/api/rag/add', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ collection: collection, path: path })
            });

            if (!response.ok) {
                const err = await response.json();
                throw new Error(err.error || 'Failed to add document');
            }

            showToast('Document added to ' + collection);
            await this.load(); // Refresh collections
        } catch (err) {
            console.error('RAG add error:', err);
            showToast('Failed to add: ' + err.message);
        }
    },

    show() {
        const historyPanel = document.getElementById('history-panel');
        const ragPanelEl = document.getElementById('rag-panel');
        if (historyPanel) historyPanel.classList.remove('active');
        if (ragPanelEl) ragPanelEl.classList.add('active');
        document.querySelectorAll('.sidebar-tab').forEach(function(tab) {
            tab.classList.toggle('active', tab.dataset.tab === 'rag');
        });
        this.load();
    },

    hide() {
        const historyPanel = document.getElementById('history-panel');
        const ragPanelEl = document.getElementById('rag-panel');
        if (ragPanelEl) ragPanelEl.classList.remove('active');
        if (historyPanel) historyPanel.classList.add('active');
        document.querySelectorAll('.sidebar-tab').forEach(function(tab) {
            tab.classList.toggle('active', tab.dataset.tab === 'history');
        });
    }
};

// ============================================================================
// WebSocket Connection
// ============================================================================

function connect() {
    const params = new URLSearchParams(window.location.search);
    sessionId = params.get('session') || `browser:${Date.now()}`;

    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(`${protocol}//${location.host}/ws?session=${sessionId}`);

    ws.onopen = () => {
        reconnectAttempts = 0;
        console.log('WebSocket connected');
        // Request history on connect
        ws.send(JSON.stringify({ type: 'getHistory' }));
    };

    ws.onmessage = (e) => {
        try {
            const msg = JSON.parse(e.data);
            handleMessage(msg);
        } catch (err) {
            console.error('Failed to parse message:', err);
        }
    };

    ws.onclose = () => {
        console.log('WebSocket closed, reconnecting...');
        // Clear pending requests - their callbacks will never fire
        pendingRequests.clear();
        setTimeout(connect, Math.min(1000 * Math.pow(2, reconnectAttempts++), 5000));
    };

    ws.onerror = (err) => {
        console.error('WebSocket error:', err);
    };
}

function handleMessage(msg) {
    switch (msg.type) {
        case 'connected':
            sessionId = msg.sessionId;
            console.log('Connected to session: ' + sessionId + ', model: ' + msg.model);
            break;

        case 'text':
            handleTextMessage(msg);
            break;

        case 'tool_start':
            addToolCall(msg.tool, msg.args, msg.tool_call_id);
            break;

        case 'tool_done':
            completeToolCall(msg.tool_call_id, msg.result);
            break;

        case 'thinking':
            addThinkingTrace(msg.content);
            break;

        case 'done':
            finalizeMessage();
            isStreaming = false;
            // Refresh history sidebar to show updated conversation
            historySidebar.load();
            break;

        case 'error':
            addError(msg.message || 'Unknown error');
            isStreaming = false;
            break;

        case 'stripped':
            // Handle stripMarkdown response
            var callback = pendingRequests.get(msg.requestId);
            if (callback) {
                callback(msg.text);
                pendingRequests.delete(msg.requestId);
            }
            break;

        case 'history':
            loadHistory(msg.messages || []);
            break;

        case 'branched':
            showToast('Branched to new session: ' + msg.newSessionId);
            break;

        case 'commandResult':
            if (msg.command === 'new') {
                clearConversation();
                showToast('New conversation started');
                // Refresh history to show the new session
                historySidebar.load();
            } else if (msg.command === 'status' && msg.data) {
                var status = msg.data;
                showToast('Model: ' + status.model + ' | Messages: ' + status.messages);
            }
            break;

        default:
            console.log('Unknown message type:', msg.type);
    }
}

function handleTextMessage(msg) {
    const emptyState = document.getElementById('empty-state');
    if (emptyState) {
        emptyState.remove();
    }

    const conversation = document.getElementById('conversation');
    let container;

    // For streaming: update existing container if same message ID
    if (msg.messageId && msg.messageId === currentMessageId) {
        container = document.getElementById('current-message');
        if (container) {
            const contentDiv = container.querySelector('.message-content');
            contentDiv.innerHTML = safeMarkdown(msg.content);
            applyCodeBlockEnhancements(contentDiv);
            // Update stored content
            const msgId = container.dataset.messageId;
            const stored = messageStore.messages.find(m => m.id === msgId);
            if (stored) stored.content = msg.content;
            window.scrollTo(0, document.body.scrollHeight);
            return;
        }
    }

    // Create new assistant message
    appendMessage('assistant', msg.content, msg.messageId);
}

function loadHistory(messages) {
    const conversation = document.getElementById('conversation');
    const emptyState = document.getElementById('empty-state');

    if (messages.length === 0) {
        if (!emptyState) {
            conversation.innerHTML = `
                <div class="empty-state" id="empty-state">
                    <h2>How can I help you today?</h2>
                    <p>Type a question below and press Ctrl+Enter to send.</p>
                </div>
            `;
        }
        return;
    }

    if (emptyState) {
        emptyState.remove();
    }

    // Clear existing messages
    messageStore.clear();
    conversation.innerHTML = '';

    // Rebuild from history
    for (const msg of messages) {
        appendMessage(msg.role, msg.content);
    }
}

// ============================================================================
// Message Display
// ============================================================================

function appendMessage(role, content, streamingId) {
    const emptyState = document.getElementById('empty-state');
    if (emptyState) {
        emptyState.remove();
    }

    const conversation = document.getElementById('conversation');
    const container = document.createElement('div');
    container.className = 'message ' + role;

    const contentDiv = document.createElement('div');
    contentDiv.className = 'message-content';
    contentDiv.innerHTML = safeMarkdown(content);
    container.appendChild(contentDiv);

    const msgId = messageStore.add(role, content, container);

    const actions = createActionIcons(role, msgId);
    container.appendChild(actions);

    if (role === 'assistant' && streamingId) {
        container.id = 'current-message';
        currentMessageId = streamingId;
        isStreaming = true;
    }

    conversation.appendChild(container);
    applyCodeBlockEnhancements(contentDiv);
    updateLastMessageIndicators();
    window.scrollTo(0, document.body.scrollHeight);
}

function finalizeMessage() {
    const current = document.getElementById('current-message');
    if (current) {
        current.removeAttribute('id');
    }
    currentMessageId = null;
    updateLastMessageIndicators();
}

// ============================================================================
// Collapsible Tool Calls
// ============================================================================

function getToolConfig(toolName) {
    return TOOL_CONFIG[toolName] || { name: toolName, icon: 'tool' };
}

function getToolPreview(toolName, args) {
    if (!args) return '';

    // Generate a short preview based on tool type
    if (toolName === 'execute_python' && args.code) {
        const firstLine = args.code.split('\n')[0];
        return firstLine.length > 50 ? firstLine.substring(0, 50) + '...' : firstLine;
    }
    if (toolName === 'sandboxed_shell' && args.command) {
        return args.command.length > 50 ? args.command.substring(0, 50) + '...' : args.command;
    }
    if (toolName === 'fetch_url' && args.url) {
        return args.url.length > 40 ? args.url.substring(0, 40) + '...' : args.url;
    }
    if (toolName === 'search_google' && args.query) {
        return args.query;
    }

    // Default: show first argument value
    const keys = Object.keys(args);
    if (keys.length > 0) {
        const val = String(args[keys[0]]);
        return val.length > 50 ? val.substring(0, 50) + '...' : val;
    }
    return '';
}

function addToolCall(toolName, args, toolCallId) {
    const conversation = document.getElementById('conversation');
    const config = getToolConfig(toolName);
    const preview = getToolPreview(toolName, args);

    const container = document.createElement('div');
    container.className = 'tool-call running';
    container.id = 'tool-' + (toolCallId || Date.now());
    currentToolCallId = container.id;

    // Header (always visible)
    const header = document.createElement('div');
    header.className = 'tool-call-header';
    header.onclick = function() { toggleToolCall(container); };

    const iconSpan = document.createElement('span');
    iconSpan.className = 'tool-call-icon';
    iconSpan.innerHTML = ICONS[config.icon] || ICONS.tool;
    header.appendChild(iconSpan);

    const nameSpan = document.createElement('span');
    nameSpan.className = 'tool-call-name';
    nameSpan.textContent = config.name;
    header.appendChild(nameSpan);

    const previewSpan = document.createElement('span');
    previewSpan.className = 'tool-call-preview';
    previewSpan.textContent = preview;
    header.appendChild(previewSpan);

    const statusSpan = document.createElement('span');
    statusSpan.className = 'tool-call-status running';
    header.appendChild(statusSpan);

    const chevronSpan = document.createElement('span');
    chevronSpan.className = 'tool-call-expand';
    chevronSpan.innerHTML = ICONS.chevron;
    header.appendChild(chevronSpan);

    container.appendChild(header);

    // Body (expandable)
    const body = document.createElement('div');
    body.className = 'tool-call-body';

    // Input section
    if (args) {
        const inputSection = document.createElement('div');
        inputSection.className = 'tool-call-section';

        const inputLabel = document.createElement('div');
        inputLabel.className = 'tool-call-section-label';
        inputLabel.textContent = 'Input';
        inputSection.appendChild(inputLabel);

        const inputContent = document.createElement('pre');
        inputContent.textContent = JSON.stringify(args, null, 2);
        inputSection.appendChild(inputContent);

        body.appendChild(inputSection);
    }

    // Output section (placeholder)
    const outputSection = document.createElement('div');
    outputSection.className = 'tool-call-section tool-call-output';

    const outputLabel = document.createElement('div');
    outputLabel.className = 'tool-call-section-label';
    outputLabel.textContent = 'Output';
    outputSection.appendChild(outputLabel);

    const outputContent = document.createElement('pre');
    outputContent.textContent = 'Running...';
    outputSection.appendChild(outputContent);

    body.appendChild(outputSection);
    container.appendChild(body);

    conversation.appendChild(container);
    window.scrollTo(0, document.body.scrollHeight);
}

function completeToolCall(toolCallId, result) {
    const id = toolCallId ? 'tool-' + toolCallId : currentToolCallId;
    const container = document.getElementById(id);

    if (container) {
        // Update status icon
        const statusIcon = container.querySelector('.tool-call-status');
        if (statusIcon) {
            statusIcon.classList.remove('running');
            statusIcon.classList.add('completed');
        }

        // Update output content
        const outputContent = container.querySelector('.tool-call-output pre');
        if (outputContent && result !== undefined) {
            const resultStr = typeof result === 'string' ? result : JSON.stringify(result, null, 2);
            outputContent.textContent = resultStr.length > 2000
                ? resultStr.substring(0, 2000) + '\n... (truncated)'
                : resultStr;
        }
    }

    currentToolCallId = null;
}

function toggleToolCall(container) {
    container.classList.toggle('expanded');
}

// ============================================================================
// Collapsible Thinking Traces
// ============================================================================

function addThinkingTrace(content) {
    const conversation = document.getElementById('conversation');

    const wrapper = document.createElement('div');
    wrapper.className = 'thinking-trace';

    const details = document.createElement('details');

    const summary = document.createElement('summary');
    // Note: CSS uses ::before pseudo-element to add the thinking icon

    const labelSpan = document.createElement('span');
    labelSpan.textContent = 'Thinking';
    summary.appendChild(labelSpan);

    // Preview (first 100 chars)
    const previewSpan = document.createElement('span');
    previewSpan.className = 'thinking-trace-preview';
    const preview = content.replace(/\s+/g, ' ').trim();
    previewSpan.textContent = preview.length > 100 ? preview.substring(0, 100) + '...' : preview;
    summary.appendChild(previewSpan);

    details.appendChild(summary);

    // Full content
    const body = document.createElement('div');
    body.className = 'thinking-trace-content';
    body.innerHTML = safeMarkdown(content);
    details.appendChild(body);

    wrapper.appendChild(details);
    conversation.appendChild(wrapper);
    window.scrollTo(0, document.body.scrollHeight);
}

function addError(message) {
    const conversation = document.getElementById('conversation');
    const errorDiv = document.createElement('div');
    errorDiv.className = 'message error';
    errorDiv.textContent = message;
    conversation.appendChild(errorDiv);
    window.scrollTo(0, document.body.scrollHeight);
}

function clearConversation() {
    const conversation = document.getElementById('conversation');
    conversation.innerHTML = `
        <div class="empty-state" id="empty-state">
            <h2>How can I help you today?</h2>
            <p>Type a question below and press Ctrl+Enter to send.</p>
        </div>
    `;
    currentMessageId = null;
    messageStore.clear();
}

// ============================================================================
// Code Block Enhancements
// ============================================================================

function applyCodeBlockEnhancements(container) {
    container.querySelectorAll('pre').forEach(pre => {
        if (!pre.parentElement.classList.contains('code-wrapper')) {
            const wrapper = document.createElement('div');
            wrapper.className = 'code-wrapper';
            pre.parentNode.insertBefore(wrapper, pre);
            wrapper.appendChild(pre);

            const copyBtn = document.createElement('button');
            copyBtn.className = 'copy-btn';
            copyBtn.innerHTML = ICONS.copy + '<span>Copy</span>';
            copyBtn.onclick = function() {
                const code = pre.querySelector('code') || pre;
                navigator.clipboard.writeText(code.textContent);
                copyBtn.innerHTML = ICONS.copy + '<span>Copied!</span>';
                setTimeout(() => {
                    copyBtn.innerHTML = ICONS.copy + '<span>Copy</span>';
                }, 1500);
            };
            wrapper.appendChild(copyBtn);
        }
    });

    container.querySelectorAll('pre code').forEach(block => {
        hljs.highlightElement(block);
    });
}

// ============================================================================
// Message Actions
// ============================================================================

function updateLastMessageIndicators() {
    document.querySelectorAll('.message.last-user, .message.last-assistant')
        .forEach(el => el.classList.remove('last-user', 'last-assistant'));

    const lastUser = messageStore.getLastByRole('user');
    const lastAssistant = messageStore.getLastByRole('assistant');

    if (lastUser?.element) lastUser.element.classList.add('last-user');
    if (lastAssistant?.element) lastAssistant.element.classList.add('last-assistant');
}

function createIconButton(iconName, tooltip, onClick) {
    const btn = document.createElement('button');
    btn.className = 'message-action-btn';
    btn.innerHTML = ICONS[iconName];
    btn.title = tooltip;
    btn.onclick = (e) => { e.stopPropagation(); onClick(); };
    return btn;
}

function createActionIcons(role, messageId) {
    const container = document.createElement('div');
    container.className = 'message-actions';

    // Copy markdown (always)
    container.appendChild(createIconButton('copy', 'Copy markdown', () => {
        const msg = messageStore.messages.find(m => m.id === messageId);
        if (msg) {
            navigator.clipboard.writeText(msg.content);
            showToast('Copied to clipboard');
        }
    }));

    // Copy plain text (strip markdown) - for assistant only
    if (role === 'assistant') {
        container.appendChild(createIconButton('copyPlain', 'Copy plain text', () => {
            const msg = messageStore.messages.find(m => m.id === messageId);
            if (msg) {
                copyPlainText(msg.content);
            }
        }));
    }

    if (role === 'user') {
        const editBtn = createIconButton('edit', 'Edit', () => showInlineEditor(messageId));
        editBtn.classList.add('action-edit');
        container.appendChild(editBtn);
    } else if (role === 'assistant') {
        const regenBtn = createIconButton('regenerate', 'Regenerate', () => regenerateResponse(messageId));
        regenBtn.classList.add('action-regenerate');
        container.appendChild(regenBtn);

        container.appendChild(createIconButton('branch', 'Branch', () => branchConversation(messageId)));
    }

    return container;
}

// Copy plain text using server-side stripMarkdown
function copyPlainText(content) {
    const requestId = crypto.randomUUID();
    pendingRequests.set(requestId, (stripped) => {
        navigator.clipboard.writeText(stripped);
        showToast('Copied plain text');
    });
    ws.send(JSON.stringify({ type: 'stripMarkdown', text: content, requestId }));
}

// ============================================================================
// Inline Editor
// ============================================================================

let activeEditor = null;

function showInlineEditor(messageId) {
    if (activeEditor) cancelInlineEditor();

    const msg = messageStore.messages.find(m => m.id === messageId);
    if (!msg || msg.role !== 'user') return;

    const container = msg.element;
    const contentDiv = container.querySelector('.message-content');

    contentDiv.style.display = 'none';

    const editor = document.createElement('div');
    editor.className = 'inline-editor';
    editor.innerHTML = `
        <textarea class="inline-editor-textarea">${escapeHtml(msg.content)}</textarea>
        <div class="inline-editor-actions">
            <button class="inline-editor-cancel">Cancel</button>
            <button class="inline-editor-save">Save & Regenerate</button>
        </div>
    `;

    container.appendChild(editor);

    const textarea = editor.querySelector('textarea');
    textarea.focus();
    textarea.setSelectionRange(textarea.value.length, textarea.value.length);

    const autoResize = () => {
        textarea.style.height = 'auto';
        textarea.style.height = textarea.scrollHeight + 'px';
    };
    autoResize();
    textarea.addEventListener('input', autoResize);

    editor.querySelector('.inline-editor-cancel').onclick = cancelInlineEditor;
    editor.querySelector('.inline-editor-save').onclick = () => {
        const newContent = textarea.value.trim();
        if (newContent) submitEditedMessage(messageId, newContent);
    };

    textarea.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') cancelInlineEditor();
        else if (e.key === 'Enter' && e.ctrlKey) {
            const newContent = textarea.value.trim();
            if (newContent) submitEditedMessage(messageId, newContent);
        }
    });

    addReadlineShortcuts(textarea, autoResize);

    activeEditor = { messageId, container, contentDiv, editor };
}

function cancelInlineEditor() {
    if (!activeEditor) return;
    activeEditor.contentDiv.style.display = '';
    activeEditor.editor.remove();
    activeEditor = null;
}

function submitEditedMessage(messageId, newContent) {
    cancelInlineEditor();

    const msg = messageStore.messages.find(m => m.id === messageId);
    if (!msg) return;

    msg.content = newContent;
    const contentDiv = msg.element.querySelector('.message-content');
    contentDiv.innerHTML = safeMarkdown(newContent);
    applyCodeBlockEnhancements(contentDiv);

    messageStore.truncateAfter(messageId);
    updateLastMessageIndicators();

    // Count completed turns before this message
    const msgIndex = messageStore.messages.findIndex(m => m.id === messageId);
    let turnCount = 0;
    for (let i = 0; i < msgIndex; i++) {
        if (messageStore.messages[i].role === 'assistant') {
            turnCount++;
        }
    }

    ws.send(JSON.stringify({
        type: 'edit',
        keepTurns: turnCount,
        newContent: newContent
    }));
}

function regenerateResponse(messageId) {
    const msg = messageStore.messages.find(m => m.id === messageId);
    if (!msg || msg.role !== 'assistant') return;

    const msgIndex = messageStore.messages.findIndex(m => m.id === messageId);
    let userMsg = null;
    for (let i = msgIndex - 1; i >= 0; i--) {
        if (messageStore.messages[i].role === 'user') {
            userMsg = messageStore.messages[i];
            break;
        }
    }
    if (!userMsg) return;

    messageStore.removeMessage(messageId);
    updateLastMessageIndicators();

    ws.send(JSON.stringify({
        type: 'regenerate',
        userContent: userMsg.content
    }));
}

function branchConversation(messageId) {
    const messages = messageStore.getMessagesUpTo(messageId);
    if (messages.length === 0) return;

    const conversationData = messages.map(m => ({
        role: m.role,
        content: m.content
    }));

    ws.send(JSON.stringify({
        type: 'branch',
        messages: conversationData
    }));
}

// ============================================================================
// Input Handling
// ============================================================================

function sendMessage() {
    const input = document.getElementById('input');
    const query = input.value.trim();

    if (!query || isStreaming) return;

    // Add user message to UI
    appendMessage('user', query);

    // Collect any pending images from drag-drop
    const images = window.pendingImages || [];
    window.pendingImages = [];

    // Send via WebSocket
    ws.send(JSON.stringify({
        type: 'query',
        query: query,
        mode: 'assistant',
        images: images
    }));

    input.value = '';
    autoResizeInput();
}

function autoResizeInput() {
    const input = document.getElementById('input');
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 200) + 'px';
}

// Add readline-style keyboard shortcuts to a textarea
function addReadlineShortcuts(textarea, onResize) {
    textarea.addEventListener('keydown', (e) => {
        if (!e.ctrlKey) return;

        if (e.key === 'w' || e.key === 'Backspace') {
            e.preventDefault();
            const pos = textarea.selectionStart;
            if (pos > 0) {
                const text = textarea.value;
                let start = pos;
                while (start > 0 && /\s/.test(text[start - 1])) start--;
                while (start > 0 && !/\s/.test(text[start - 1])) start--;
                textarea.value = text.slice(0, start) + text.slice(pos);
                textarea.setSelectionRange(start, start);
                if (onResize) onResize();
            }
        }
        else if (e.key === 'u') {
            e.preventDefault();
            const pos = textarea.selectionStart;
            const text = textarea.value;
            let lineStart = text.lastIndexOf('\n', pos - 1) + 1;
            textarea.value = text.slice(0, lineStart) + text.slice(pos);
            textarea.setSelectionRange(lineStart, lineStart);
            if (onResize) onResize();
        }
        else if (e.key === 'a') {
            e.preventDefault();
            const pos = textarea.selectionStart;
            const text = textarea.value;
            let lineStart = text.lastIndexOf('\n', pos - 1) + 1;
            textarea.setSelectionRange(lineStart, lineStart);
        }
        else if (e.key === 'e') {
            e.preventDefault();
            const pos = textarea.selectionStart;
            const text = textarea.value;
            let lineEnd = text.indexOf('\n', pos);
            if (lineEnd === -1) lineEnd = text.length;
            textarea.setSelectionRange(lineEnd, lineEnd);
        }
    });
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// ============================================================================
// Toast Notification
// ============================================================================

function showToast(message) {
    let toast = document.getElementById('toast');
    toast.textContent = message;
    toast.classList.add('show');
    setTimeout(() => toast.classList.remove('show'), 2000);
}

// ============================================================================
// Action Panel (Ctrl+K)
// ============================================================================

const actionPanelActions = [
    { id: 'copy-response', label: 'Copy last response', shortcut: '', action: copyLastResponse },
    { id: 'copy-all', label: 'Copy entire conversation', shortcut: '', action: copyAllMessages },
    { id: 'new-session', label: 'New conversation', shortcut: '', action: startNewSession },
    { id: 'status', label: 'Show session status', shortcut: '', action: showStatus },
];

let actionPanelVisible = false;
let selectedActionIndex = 0;

function showActionPanel() {
    const panel = document.getElementById('action-panel');
    const search = document.getElementById('action-search');
    const list = document.getElementById('action-list');

    panel.classList.remove('hidden');
    actionPanelVisible = true;
    selectedActionIndex = 0;
    search.value = '';

    renderActionList();
    search.focus();
}

function hideActionPanel() {
    const panel = document.getElementById('action-panel');
    panel.classList.add('hidden');
    actionPanelVisible = false;
}

function renderActionList(filter = '') {
    const list = document.getElementById('action-list');
    const filterLower = filter.toLowerCase();

    const filtered = actionPanelActions.filter(a =>
        a.label.toLowerCase().includes(filterLower)
    );

    list.innerHTML = '';
    filtered.forEach((action, i) => {
        const item = document.createElement('div');
        item.className = 'action-item' + (i === selectedActionIndex ? ' selected' : '');
        item.innerHTML = `<span>${action.label}</span>`;
        item.onclick = () => {
            action.action();
            hideActionPanel();
        };
        list.appendChild(item);
    });
}

function copyLastResponse() {
    const lastAssistant = messageStore.getLastByRole('assistant');
    if (lastAssistant) {
        navigator.clipboard.writeText(lastAssistant.content);
        showToast('Copied last response');
    }
}

function copyAllMessages() {
    const text = messageStore.messages.map(m =>
        `${m.role === 'user' ? 'You' : 'Assistant'}: ${m.content}`
    ).join('\n\n');
    navigator.clipboard.writeText(text);
    showToast('Copied conversation');
}

function startNewSession() {
    ws.send(JSON.stringify({ type: 'command', command: 'new' }));
}

function showStatus() {
    ws.send(JSON.stringify({ type: 'command', command: 'status' }));
}

// ============================================================================
// Event Listeners
// ============================================================================

document.addEventListener('DOMContentLoaded', function() {
    var input = document.getElementById('input');
    var sendBtn = document.getElementById('send-btn');
    var actionSearch = document.getElementById('action-search');
    var actionBackdrop = document.querySelector('.action-panel-backdrop');

    // Initialize modules
    historySidebar.init();
    captureControls.init();

    // Input handling
    input.addEventListener('input', function() {
        autoResizeInput();
        atAutocomplete.handleInput(input);
    });

    input.addEventListener('keydown', function(e) {
        // Handle @ autocomplete navigation
        if (atAutocomplete.visible) {
            if (e.key === 'ArrowDown') {
                e.preventDefault();
                atAutocomplete.navigate(1);
                return;
            } else if (e.key === 'ArrowUp') {
                e.preventDefault();
                atAutocomplete.navigate(-1);
                return;
            } else if (e.key === 'Enter' && !e.ctrlKey) {
                e.preventDefault();
                atAutocomplete.select(atAutocomplete.selectedIndex);
                return;
            } else if (e.key === 'Escape') {
                atAutocomplete.hide();
                return;
            } else if (e.key === 'Tab') {
                e.preventDefault();
                atAutocomplete.select(atAutocomplete.selectedIndex);
                return;
            }
        }

        // Send message with Ctrl+Enter
        if (e.key === 'Enter' && e.ctrlKey) {
            e.preventDefault();
            sendMessage();
        }
    });
    addReadlineShortcuts(input, autoResizeInput);

    // Send button
    sendBtn.addEventListener('click', sendMessage);

    // Sidebar toggle buttons
    var sidebarToggle = document.getElementById('sidebar-toggle');
    var sidebarToggleInput = document.getElementById('sidebar-toggle-input');
    if (sidebarToggle) {
        sidebarToggle.addEventListener('click', function() {
            historySidebar.toggle();
        });
    }
    if (sidebarToggleInput) {
        sidebarToggleInput.addEventListener('click', function() {
            historySidebar.toggle();
        });
    }

    // Sidebar tab switching
    document.querySelectorAll('.sidebar-tab').forEach(function(tab) {
        tab.addEventListener('click', function() {
            if (tab.dataset.tab === 'rag') {
                ragPanel.show();
            } else {
                ragPanel.hide();
            }
        });
    });

    // History search
    var historySearch = document.getElementById('history-search');
    if (historySearch) {
        var searchTimeout = null;
        historySearch.addEventListener('input', function(e) {
            clearTimeout(searchTimeout);
            searchTimeout = setTimeout(function() {
                historySidebar.search(e.target.value);
            }, 300);
        });
    }

    // @ button
    var atBtn = document.getElementById('at-btn');
    if (atBtn) {
        atBtn.addEventListener('click', function() {
            input.value += '@';
            input.focus();
            atAutocomplete.handleInput(input);
        });
    }

    // Capture button and dropdown
    var captureBtn = document.getElementById('capture-btn');
    if (captureBtn) {
        captureBtn.addEventListener('click', function(e) {
            e.stopPropagation();
            captureControls.toggleDropdown();
        });
    }

    // Capture mode buttons
    document.querySelectorAll('.capture-mode').forEach(function(btn) {
        btn.addEventListener('click', function() {
            captureControls.capture(btn.dataset.mode);
        });
    });

    // RAG add button
    var ragAddBtn = document.getElementById('rag-add-btn');
    var ragAddInput = document.getElementById('rag-add-input');
    if (ragAddBtn && ragAddInput) {
        ragAddBtn.addEventListener('click', function() {
            ragPanel.addDocument(ragAddInput.value);
            ragAddInput.value = '';
        });
        ragAddInput.addEventListener('keydown', function(e) {
            if (e.key === 'Enter') {
                ragPanel.addDocument(ragAddInput.value);
                ragAddInput.value = '';
            }
        });
    }

    // New conversation button (sidebar)
    var newConvBtn = document.getElementById('new-conversation-btn');
    if (newConvBtn) {
        newConvBtn.addEventListener('click', function() {
            startNewSession();
        });
    }

    // New conversation button (input controls)
    var newConvBtnInput = document.getElementById('new-conversation-btn-input');
    if (newConvBtnInput) {
        newConvBtnInput.addEventListener('click', function() {
            startNewSession();
        });
    }

    // Close dropdowns when clicking outside
    document.addEventListener('click', function(e) {
        // Close capture dropdown
        if (!e.target.closest('#capture-wrapper')) {
            captureControls.hideDropdown();
        }
        // Close @ autocomplete
        if (!e.target.closest('#at-autocomplete') && !e.target.closest('#input')) {
            atAutocomplete.hide();
        }
    });

    // Global keyboard shortcuts
    document.addEventListener('keydown', function(e) {
        // Ctrl+K - Action panel
        if (e.ctrlKey && e.key === 'k') {
            e.preventDefault();
            if (actionPanelVisible) {
                hideActionPanel();
            } else {
                showActionPanel();
            }
        }

        // Escape
        if (e.key === 'Escape') {
            if (actionPanelVisible) {
                hideActionPanel();
            }
            atAutocomplete.hide();
            captureControls.hideDropdown();
        }
    });

    // Action panel search
    if (actionSearch) {
        actionSearch.addEventListener('input', function(e) {
            selectedActionIndex = 0;
            renderActionList(e.target.value);
        });

        actionSearch.addEventListener('keydown', function(e) {
            var list = document.getElementById('action-list');
            var items = list.querySelectorAll('.action-item');

            if (e.key === 'ArrowDown') {
                e.preventDefault();
                selectedActionIndex = Math.min(selectedActionIndex + 1, items.length - 1);
                renderActionList(actionSearch.value);
            } else if (e.key === 'ArrowUp') {
                e.preventDefault();
                selectedActionIndex = Math.max(selectedActionIndex - 1, 0);
                renderActionList(actionSearch.value);
            } else if (e.key === 'Enter') {
                e.preventDefault();
                var selected = items[selectedActionIndex];
                if (selected) selected.click();
            } else if (e.key === 'Escape') {
                hideActionPanel();
            }
        });
    }

    // Action panel backdrop click
    if (actionBackdrop) {
        actionBackdrop.addEventListener('click', hideActionPanel);
    }

    // Connect WebSocket
    connect();
});
