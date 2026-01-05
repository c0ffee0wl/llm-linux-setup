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
if (typeof marked === 'undefined' || typeof hljs === 'undefined' || typeof DOMPurify === 'undefined' || typeof mermaid === 'undefined') {
    document.body.innerHTML = '<div style="padding: 20px; color: #c00;">' +
        '<h3>JavaScript assets failed to load</h3>' +
        '<p>Missing: ' +
        (typeof marked === 'undefined' ? 'marked.js ' : '') +
        (typeof hljs === 'undefined' ? 'highlight.js ' : '') +
        (typeof DOMPurify === 'undefined' ? 'purify.js ' : '') +
        (typeof mermaid === 'undefined' ? 'mermaid.js' : '') +
        '</p>' +
        '<p>Run: <code>./install-llm-tools.sh</code> to download assets.</p>' +
        '</div>';
    throw new Error('Required JavaScript libraries not loaded');
}

// Initialize Mermaid
mermaid.initialize({
    startOnLoad: false,
    theme: 'default',
    securityLevel: 'strict',
    fontFamily: 'monospace',
    suppressErrorRendering: true  // Don't show error overlay in DOM
});

// Helper function to safely render markdown with DOMPurify sanitization
function safeMarkdown(content) {
    return DOMPurify.sanitize(marked.parse(content), {
        ALLOWED_TAGS: ['p', 'br', 'strong', 'em', 'code', 'pre', 'ul', 'ol', 'li', 'a', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'blockquote', 'hr', 'table', 'thead', 'tbody', 'tr', 'th', 'td', 'span', 'div',
            // SVG elements for mermaid diagrams
            'svg', 'g', 'path', 'rect', 'circle', 'ellipse', 'line', 'polyline', 'polygon', 'text', 'tspan', 'defs', 'clipPath', 'marker', 'foreignObject', 'style'],
        ALLOWED_ATTR: ['href', 'title', 'class', 'target', 'rel',
            // SVG attributes for mermaid
            'viewBox', 'width', 'height', 'd', 'fill', 'stroke', 'stroke-width', 'stroke-linecap', 'stroke-linejoin', 'transform', 'x', 'y', 'x1', 'y1', 'x2', 'y2', 'cx', 'cy', 'r', 'rx', 'ry', 'points', 'font-size', 'font-family', 'text-anchor', 'dominant-baseline', 'clip-path', 'marker-end', 'marker-start', 'id', 'style', 'xmlns', 'aria-roledescription', 'role'],
        ALLOW_DATA_ATTR: false
    });
}

// Configure marked for safe rendering
marked.setOptions({
    breaks: true,
    gfm: true
});

// Security: Strip raw HTML to prevent XSS attacks from LLM responses
// Also configure links to open in new tabs
const renderer = new marked.Renderer();
renderer.html = function(token) {
    return '';
};
renderer.link = function({ href, title, tokens }) {
    const text = this.parser.parseInline(tokens);
    const titleAttr = title ? ` title="${title}"` : '';
    // Open external links in new tab with security attributes
    if (href && (href.startsWith('http://') || href.startsWith('https://'))) {
        return `<a href="${href}"${titleAttr} target="_blank" rel="noopener noreferrer">${text}</a>`;
    }
    return `<a href="${href}"${titleAttr}>${text}</a>`;
};
marked.use({ renderer });

// ============================================================================
// State
// ============================================================================

let ws = null;
let reconnectAttempts = 0;
let connectionState = 'disconnected'; // 'connected', 'connecting', 'disconnected', 'server_down'
let currentMessageId = null;
let sessionId = null;
let isStreaming = false;
let currentToolCallId = null;
let isHistoricalView = false;  // True when viewing a historical conversation (not the active session)
let viewedConversationId = null;  // ID of the currently viewed historical conversation
let currentConversationId = null;  // ID of the active conversation (set when done message received)

/**
 * Reset input area to active state (removes read-only restrictions).
 * Called when starting new conversation or loading active/GUI-origin conversation.
 */
function resetInputToActive() {
    const conversation = document.getElementById('conversation');
    if (conversation) {
        conversation.classList.remove('historical');
        conversation.classList.remove('non-gui-origin');
    }
    const input = document.getElementById('input');
    if (input) {
        input.placeholder = 'Ask a question...';
    }
}

// Pending requests for async responses (e.g., stripMarkdown)
const pendingRequests = new Map();

// Pending images for capture/upload
window.pendingImages = [];

// Attachment panel state
const attachmentPanel = {
    items: [],  // {path, type, thumbnail}
    visible: false,

    add(path, type) {
        // Generate thumbnail data URL for image files
        const item = { path, type: type || 'image', thumbnail: null };

        // For server screenshots, we can fetch a thumbnail
        // For pasted images, thumbnail is provided by caller
        this.items.push(item);
        this.render();
    },

    addWithThumbnail(path, type, thumbnail) {
        this.items.push({ path, type: type || 'image', thumbnail });
        this.render();
    },

    remove(index) {
        this.items.splice(index, 1);
        this.render();
    },

    clear() {
        this.items = [];
        this.render();
    },

    toggle() {
        this.visible = !this.visible;
        const panel = document.getElementById('attachment-panel');
        if (panel) {
            panel.classList.toggle('hidden', !this.visible);
        }
    },

    hide() {
        this.visible = false;
        const panel = document.getElementById('attachment-panel');
        if (panel) {
            panel.classList.add('hidden');
        }
    },

    render() {
        const btn = document.getElementById('attachment-btn');
        const count = document.getElementById('attachment-count');
        const list = document.getElementById('attachment-list');

        if (!btn || !count || !list) return;

        // Show/hide button based on item count
        if (this.items.length === 0) {
            btn.classList.add('hidden');
            this.hide();
        } else {
            btn.classList.remove('hidden');
            count.textContent = this.items.length;
        }

        // Clear and rebuild list
        list.textContent = '';

        for (let i = 0; i < this.items.length; i++) {
            const item = this.items[i];
            const div = document.createElement('div');
            div.className = 'attachment-item';

            // Thumbnail
            const thumb = document.createElement('img');
            thumb.className = 'attachment-thumb';
            if (item.thumbnail) {
                thumb.src = item.thumbnail;
            } else {
                // Placeholder or fetch from server
                thumb.src = '/api/thumbnail?path=' + encodeURIComponent(item.path);
            }
            thumb.alt = 'Attachment';
            div.appendChild(thumb);

            // Info (filename)
            const info = document.createElement('span');
            info.className = 'attachment-info';
            const filename = item.path.split('/').pop() || item.path;
            info.textContent = filename;
            info.title = item.path;
            div.appendChild(info);

            // Delete button
            const deleteBtn = document.createElement('button');
            deleteBtn.className = 'attachment-delete';
            deleteBtn.title = 'Remove';
            deleteBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>';
            deleteBtn.onclick = (e) => {
                e.stopPropagation();
                this.remove(i);
            };
            div.appendChild(deleteBtn);

            list.appendChild(div);
        }
    },

    // Get paths for sending
    getPaths() {
        return this.items.map(item => item.path);
    }
};

// Helper to check WebSocket connection state
function isConnected() {
    return ws && ws.readyState === WebSocket.OPEN;
}

// Safe WebSocket send with error handling
function safeSend(data) {
    if (!isConnected()) {
        showToast('Not connected to server');
        return false;
    }
    try {
        ws.send(JSON.stringify(data));
        return true;
    } catch (e) {
        console.error('WebSocket send failed:', e);
        showToast('Connection lost');
        return false;
    }
}

// Helper to scroll conversation to bottom
function scrollToBottom() {
    const conversation = document.getElementById('conversation');
    if (conversation) {
        conversation.scrollTop = conversation.scrollHeight;
    }
}

// Strip context tags from content for display (keeps original for model)
function stripContextTags(content) {
    if (!content) return content;
    // Strip gui_context, terminal_context, retrieved_documents, context (legacy)
    return content
        .replace(/<gui_context>[\s\S]*?<\/gui_context>\s*/g, '')
        .replace(/<terminal_context>[\s\S]*?<\/terminal_context>\s*/g, '')
        .replace(/<retrieved_documents>[\s\S]*?<\/retrieved_documents>\s*/g, '')
        .replace(/<context>[\s\S]*?<\/context>\s*/g, '')
        .trim();
}

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
    fork: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="18" r="3"/><circle cx="6" cy="6" r="3"/><circle cx="18" cy="6" r="3"/><path d="M18 9v1a2 2 0 01-2 2H8a2 2 0 01-2-2V9"/><line x1="12" y1="12" x2="12" y2="15"/></svg>',
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

                const itemContent = document.createElement('div');
                itemContent.className = 'history-item-content';
                itemContent.onclick = () => this.loadConversation(conv.id);

                const itemHeader = document.createElement('div');
                itemHeader.className = 'history-item-header';

                const preview = document.createElement('span');
                preview.className = 'history-preview';
                preview.textContent = conv.preview || 'Empty conversation';
                itemHeader.appendChild(preview);

                itemContent.appendChild(itemHeader);

                const meta = document.createElement('div');
                meta.className = 'history-meta';
                meta.textContent = (conv.message_count || 0) + ' msgs';
                itemContent.appendChild(meta);

                item.appendChild(itemContent);

                // Delete button
                const deleteBtn = document.createElement('button');
                deleteBtn.className = 'history-delete-btn';
                deleteBtn.title = 'Delete conversation';
                deleteBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"/></svg>';
                deleteBtn.onclick = (e) => {
                    e.stopPropagation();
                    this.deleteConversation(conv.id);
                };
                item.appendChild(deleteBtn);

                group.appendChild(item);
            }

            list.appendChild(group);
        }
    },

    async loadConversation(id) {
        try {
            const response = await fetch('/api/history/' + id);
            if (!response.ok) throw new Error('Failed to load conversation');
            const data = await response.json();

            // Track which conversation we're viewing
            isHistoricalView = true;
            viewedConversationId = id;
            const conversation = document.getElementById('conversation');
            if (conversation) {
                conversation.classList.add('historical');
                // Check if conversation originated from GUI
                // Non-GUI conversations (cli, tui, api) are read-only
                if (data.source !== 'gui') {
                    conversation.classList.add('non-gui-origin');
                } else {
                    conversation.classList.remove('non-gui-origin');
                    // Resume GUI conversation on server for edit/regenerate support
                    if (ws && ws.readyState === WebSocket.OPEN) {
                        safeSend({
                            type: 'resumeConversation',
                            conversationId: id
                        });
                    }
                }
            }

            // Load into main view
            loadHistory(data.messages || []);

            // Update input placeholder based on origin
            const inputEl = document.getElementById('input');
            if (inputEl) {
                inputEl.placeholder = data.source === 'gui'
                    ? 'Ask a question...'
                    : 'Read-only: conversation from CLI';
            }

            const toastMsg = data.source === 'gui'
                ? 'Resumed conversation'
                : 'Loaded conversation (read-only)';
            showToast(toastMsg);

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

    async deleteConversation(id) {
        if (!confirm('Delete this conversation? This cannot be undone.')) {
            return;
        }

        try {
            const response = await fetch('/api/history/' + id, {
                method: 'DELETE'
            });
            if (!response.ok) throw new Error('Failed to delete conversation');

            showToast('Conversation deleted');

            // If the deleted conversation was being viewed, reset the chat display
            if (viewedConversationId === id) {
                clearConversation();
            }

            // Reload history to reflect the change
            await this.load();
        } catch (err) {
            console.error('Delete conversation error:', err);
            showToast('Failed to delete conversation');
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

    _requestId: 0,

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

        // Fetch suggestions with race condition protection
        const requestId = ++this._requestId;
        const suggestions = await this.getSuggestions(prefix);

        // Ignore stale responses from earlier requests
        if (requestId !== this._requestId) return;

        this.suggestions = suggestions;
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
                let errorMsg = 'Capture failed';
                try {
                    const err = await response.json();
                    errorMsg = err.error || errorMsg;
                } catch {
                    // Response wasn't JSON, use status text
                    errorMsg = response.statusText || errorMsg;
                }
                throw new Error(errorMsg);
            }

            const data = await response.json();
            if (data.path) {
                window.pendingImages.push(data.path);
                attachmentPanel.add(data.path, 'screenshot');
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
    sources: true,

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
            empty.textContent = 'No collections. Create one above or add documents.';
            container.appendChild(empty);
            return;
        }

        for (const coll of this.collections) {
            const div = document.createElement('div');
            div.className = 'rag-collection' + (coll.name === this.activeCollection ? ' active' : '');

            // Content area (clickable to activate)
            const content = document.createElement('div');
            content.className = 'rag-collection-content';
            content.onclick = () => this.activate(coll.name);

            const name = document.createElement('span');
            name.className = 'rag-collection-name';
            name.textContent = coll.name;
            content.appendChild(name);

            const info = document.createElement('span');
            info.className = 'rag-collection-info';
            // Now shows "X docs, Y chunks" format
            const docs = coll.documents || 0;
            const chunks = coll.chunks || 0;
            info.textContent = docs + ' docs, ' + chunks + ' chunks';
            content.appendChild(info);

            div.appendChild(content);

            // Delete button (hover visibility)
            const deleteBtn = document.createElement('button');
            deleteBtn.className = 'rag-collection-delete';
            deleteBtn.title = 'Delete collection';
            deleteBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"/></svg>';
            deleteBtn.onclick = (e) => {
                e.stopPropagation();
                this.deleteCollection(coll.name);
            };
            div.appendChild(deleteBtn);

            container.appendChild(div);
        }

        // Update add section disabled state
        this.updateAddSectionState();
    },

    /**
     * Update the disabled state of RAG panel controls based on active collection.
     * - New collection input: disabled when a collection IS active
     * - Add document input: disabled when NO collection is active
     * - Show sources checkbox: ALWAYS enabled (affects both RAG and web search)
     */
    updateAddSectionState() {
        const newNameInput = document.getElementById('rag-new-name');
        const createBtn = document.getElementById('rag-create-btn');
        const addInput = document.getElementById('rag-add-input');
        const addBtn = document.getElementById('rag-add-btn');
        const hasActiveCollection = !!this.activeCollection;

        // New collection: disabled when a collection is already active
        if (newNameInput) {
            newNameInput.disabled = hasActiveCollection;
            newNameInput.placeholder = hasActiveCollection
                ? 'Deselect collection first'
                : 'New collection name...';
        }
        if (createBtn) {
            createBtn.disabled = hasActiveCollection;
        }

        // Add document: disabled when no collection is active
        if (addInput) {
            addInput.disabled = !hasActiveCollection;
            addInput.placeholder = hasActiveCollection
                ? 'Path, URL, or git:...'
                : 'Select or create a collection first';
        }
        if (addBtn) {
            addBtn.disabled = !hasActiveCollection;
        }

        // Note: Show sources checkbox is always enabled (affects RAG and web search)
    },

    async createCollection(name) {
        if (!name.trim()) return;

        // Show loading state
        const createBtn = document.getElementById('rag-create-btn');
        const nameInput = document.getElementById('rag-new-name');
        const originalBtnText = createBtn ? createBtn.textContent : '+';
        if (createBtn) {
            createBtn.textContent = '...';
            createBtn.disabled = true;
        }
        if (nameInput) nameInput.disabled = true;

        try {
            const response = await fetch('/api/rag/create', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: name.trim() })
            });

            if (!response.ok) {
                const err = await response.json();
                throw new Error(err.error || 'Failed to create collection');
            }

            showToast('Collection "' + name + '" created');
            if (nameInput) nameInput.value = ''; // Clear input on success
            await this.load(); // Refresh collections
        } catch (err) {
            console.error('RAG create error:', err);
            showToast('Failed to create: ' + err.message);
        } finally {
            // Restore button state
            if (createBtn) {
                createBtn.textContent = originalBtnText;
                createBtn.disabled = !!this.activeCollection;
            }
            if (nameInput) nameInput.disabled = !!this.activeCollection;
        }
    },

    async deleteCollection(name) {
        if (!confirm('Delete collection "' + name + '"? This will remove all documents and cannot be undone.')) {
            return;
        }

        showToast('Deleting collection...');

        try {
            const response = await fetch('/api/rag/delete/' + encodeURIComponent(name), {
                method: 'DELETE'
            });

            if (!response.ok) {
                const err = await response.json();
                throw new Error(err.error || 'Failed to delete collection');
            }

            // If this was the active collection, clear it
            if (this.activeCollection === name) {
                this.activeCollection = null;
            }

            showToast('Collection "' + name + '" deleted');
            await this.load(); // Refresh collections
        } catch (err) {
            console.error('RAG delete error:', err);
            showToast('Failed to delete: ' + err.message);
        }
    },

    async activate(name) {
        // Toggle: if clicking on already active collection, deactivate it
        const isDeactivating = this.activeCollection === name;
        const newCollection = isDeactivating ? null : name;

        try {
            const response = await fetch('/api/rag/activate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    session: sessionId,
                    collection: newCollection,
                    sources: this.sources
                })
            });

            if (!response.ok) throw new Error('Failed to update collection');

            this.activeCollection = newCollection;
            this.render();
            showToast(isDeactivating ? 'RAG deactivated' : 'RAG: ' + name + ' activated');
        } catch (err) {
            console.error('RAG activate error:', err);
            showToast('Failed to update collection');
        }
    },

    async updateSources(value) {
        this.sources = value;
        // Always update the server (affects both RAG and web search)
        try {
            await fetch('/api/rag/activate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    session: sessionId,
                    collection: this.activeCollection,  // May be null
                    sources: this.sources
                })
            });
        } catch (err) {
            console.error('Failed to update sources:', err);
        }
    },

    async addDocument(path) {
        if (!path.trim()) return;

        // Require an active collection
        if (!this.activeCollection) {
            showToast('Select or create a collection first');
            return;
        }

        // Show loading state
        const addBtn = document.getElementById('rag-add-btn');
        const addInput = document.getElementById('rag-add-input');
        const originalBtnText = addBtn ? addBtn.textContent : '+';
        if (addBtn) {
            addBtn.textContent = '...';
            addBtn.disabled = true;
        }
        if (addInput) addInput.disabled = true;

        try {
            showToast('Adding to ' + this.activeCollection + '...');

            const response = await fetch('/api/rag/add', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ collection: this.activeCollection, path: path })
            });

            const result = await response.json();

            if (!response.ok) {
                throw new Error(result.error || 'Failed to add document');
            }

            // Show detailed result based on status
            if (result.status === 'success') {
                const chunks = result.chunks || 0;
                showToast(`Added ${chunks} chunk${chunks !== 1 ? 's' : ''} to ${this.activeCollection}`);
            } else if (result.status === 'skipped') {
                showToast('Already indexed: ' + (result.reason || path));
            } else if (result.status === 'error') {
                showToast('Error: ' + (result.error || 'Unknown error'));
            } else {
                showToast('Document processed');
            }

            await this.load(); // Refresh collections
        } catch (err) {
            console.error('RAG add error:', err);
            showToast('Failed to add: ' + err.message);
        } finally {
            // Restore button state
            if (addBtn) {
                addBtn.textContent = originalBtnText;
                addBtn.disabled = !this.activeCollection;
            }
            if (addInput) addInput.disabled = !this.activeCollection;
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
// Model Picker
// ============================================================================

const modelPicker = {
    models: [],
    currentModel: null,
    currentProvider: null,
    STORAGE_KEY: 'llm-guiassistant-model',

    init() {
        const select = document.getElementById('model-select');
        if (!select) return;

        select.addEventListener('change', (e) => {
            this.setModel(e.target.value);
        });

        // Try to restore saved model preference
        const savedModel = localStorage.getItem(this.STORAGE_KEY);
        if (savedModel) {
            // Will be applied when connection is established
            this._pendingModel = savedModel;
        }
    },

    setModelsFromServer(modelId) {
        // If we have a saved preference and it's different, try to restore it
        if (this._pendingModel && this._pendingModel !== modelId) {
            const savedModel = this._pendingModel;
            delete this._pendingModel;
            // Only restore if same provider (to respect provider restrictions)
            if (this.getProvider(savedModel) === this.getProvider(modelId)) {
                this.setModel(savedModel);
                return;
            }
        }

        this.currentModel = modelId;
        this.currentProvider = this.getProvider(modelId);
        // Fetch available models for the same provider
        this.loadModels();
    },

    getProvider(modelId) {
        if (!modelId) return null;
        // Extract provider prefix (e.g., "azure/gpt-4.1" → "azure")
        if (modelId.includes('/')) {
            return modelId.split('/')[0];
        }
        // Default providers based on known prefixes
        if (modelId.startsWith('gpt-')) return 'openai';
        if (modelId.startsWith('gemini')) return 'gemini';
        if (modelId.startsWith('claude')) return 'anthropic';
        return null;
    },

    async loadModels() {
        const select = document.getElementById('model-select');
        if (!select) return;

        try {
            // Fetch models filtered by current provider
            let url = '/api/models';
            if (this.currentProvider) {
                url += `?provider=${encodeURIComponent(this.currentProvider)}`;
            }

            const response = await fetch(url);
            if (!response.ok) throw new Error('Failed to fetch models');

            const data = await response.json();
            this.models = data.models || [];

            select.innerHTML = '';

            // Sort models: current model first, then alphabetically
            const sorted = [...this.models].sort((a, b) => {
                if (a.id === this.currentModel) return -1;
                if (b.id === this.currentModel) return 1;
                return a.id.localeCompare(b.id);
            });

            for (const model of sorted) {
                const option = document.createElement('option');
                option.value = model.id;
                option.textContent = model.id;
                option.selected = model.id === this.currentModel;
                select.appendChild(option);
            }

            // If current model not in list, add it at the top
            if (this.currentModel && !sorted.find(m => m.id === this.currentModel)) {
                const option = document.createElement('option');
                option.value = this.currentModel;
                option.textContent = this.currentModel;
                option.selected = true;
                select.insertBefore(option, select.firstChild);
            }
        } catch (error) {
            console.error('Error loading models:', error);
            // Fallback: just show current model
            select.innerHTML = '';
            if (this.currentModel) {
                const option = document.createElement('option');
                option.value = this.currentModel;
                option.textContent = this.currentModel;
                option.selected = true;
                select.appendChild(option);
            }
        }
    },

    setModel(modelId) {
        if (!modelId || modelId === this.currentModel) return;

        safeSend({
            type: 'command',
            command: 'model',
            args: modelId
        });
    },

    handleModelChange(modelId) {
        this.currentModel = modelId;
        this.currentProvider = this.getProvider(modelId);
        // Persist selection to localStorage
        localStorage.setItem(this.STORAGE_KEY, modelId);
        this.loadModels();
    }
};

// ============================================================================
// Temporary Chat Toggle
// ============================================================================

const tempChatToggle = {
    enabled: false,
    conversationStarted: false,

    init() {
        const checkbox = document.getElementById('temp-chat-checkbox');
        const label = document.getElementById('temp-chat-toggle');
        if (!checkbox || !label) return;

        checkbox.addEventListener('change', (e) => {
            if (!this.conversationStarted) {
                this.enabled = e.target.checked;
            } else {
                // Revert if conversation already started
                e.target.checked = this.enabled;
            }
        });

        this.updateUI();
    },

    markConversationStarted() {
        this.conversationStarted = true;
        this.updateUI();
    },

    reset() {
        this.conversationStarted = false;
        this.enabled = false;
        const checkbox = document.getElementById('temp-chat-checkbox');
        if (checkbox) {
            checkbox.checked = false;
        }
        this.updateUI();
    },

    updateUI() {
        const checkbox = document.getElementById('temp-chat-checkbox');
        const label = document.getElementById('temp-chat-toggle');
        if (!checkbox || !label) return;

        if (this.conversationStarted) {
            checkbox.disabled = true;
            label.classList.add('disabled');
            label.title = 'Cannot change after conversation started';
        } else {
            checkbox.disabled = false;
            label.classList.remove('disabled');
            label.title = 'Disable logging for this conversation (only available before first message)';
        }
    },

    isEnabled() {
        return this.enabled;
    }
};

// ============================================================================
// WebSocket Connection
// ============================================================================

/**
 * Check if the server is running via health endpoint.
 * @returns {Promise<boolean>} True if server is healthy
 */
async function checkServerHealth() {
    try {
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 3000);
        const response = await fetch('/api/health', { signal: controller.signal });
        clearTimeout(timeout);
        return response.ok;
    } catch {
        return false;
    }
}

/**
 * Update connection state and UI.
 * @param {string} state - 'connected', 'connecting', 'disconnected', 'server_down'
 */
function setConnectionState(state) {
    connectionState = state;
    updateConnectionUI();
}

/**
 * Update UI based on connection state.
 */
function updateConnectionUI() {
    // Remove any existing connection status element
    let statusEl = document.getElementById('connection-status');

    if (connectionState === 'connected') {
        // Remove status element when connected
        if (statusEl) statusEl.remove();
        return;
    }

    // Create status element if not exists
    if (!statusEl) {
        statusEl = document.createElement('div');
        statusEl.id = 'connection-status';
        // Insert after header bar (second child of main-content)
        const headerBar = document.getElementById('header-bar');
        if (headerBar && headerBar.parentNode) {
            headerBar.parentNode.insertBefore(statusEl, headerBar.nextSibling);
        }
    }

    // Update content based on state
    if (connectionState === 'connecting') {
        statusEl.className = 'connection-status connecting';
        statusEl.innerHTML = '<span class="status-icon">⟳</span> Connecting to server...';
    } else if (connectionState === 'server_down') {
        statusEl.className = 'connection-status server-down';
        statusEl.innerHTML = `
            <div class="status-content">
                <span class="status-icon">⚠</span>
                <span class="status-text">Server not running</span>
            </div>
            <div class="status-actions">
                <button onclick="retryConnection()" class="retry-btn">Retry Connection</button>
                <button onclick="copyRestartCommand()" class="copy-cmd-btn" title="Copy restart command">Copy Command</button>
            </div>
            <div class="status-hint">Run: <code>llm-assistant --daemon</code> to start the server</div>
        `;
    } else {
        statusEl.className = 'connection-status disconnected';
        statusEl.innerHTML = `
            <span class="status-icon">○</span>
            <span class="status-text">Disconnected - reconnecting (${reconnectAttempts}/10)...</span>
        `;
    }
}

/**
 * Copy the restart command to clipboard.
 */
function copyRestartCommand() {
    navigator.clipboard.writeText('llm-assistant --daemon').then(() => {
        showToast('Restart command copied to clipboard');
    }).catch(() => {
        showToast('Failed to copy command');
    });
}

/**
 * Manually retry connection after server_down state.
 */
async function retryConnection() {
    setConnectionState('connecting');
    reconnectAttempts = 0;

    // First check if server is back up
    const healthy = await checkServerHealth();
    if (healthy) {
        connect();
    } else {
        setConnectionState('server_down');
        showToast('Server still not responding. Start the daemon first.');
    }
}

function connect() {
    const params = new URLSearchParams(window.location.search);
    sessionId = params.get('session') || `browser:${Date.now()}`;

    setConnectionState('connecting');

    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(`${protocol}//${location.host}/ws?session=${sessionId}`);

    ws.onopen = () => {
        reconnectAttempts = 0;
        setConnectionState('connected');
        console.log('WebSocket connected');
        // Request history on connect
        safeSend({ type: 'getHistory' });
    };

    ws.onmessage = (e) => {
        try {
            const msg = JSON.parse(e.data);
            handleMessage(msg);
        } catch (err) {
            console.error('Failed to parse message:', err);
        }
    };

    ws.onclose = async () => {
        console.log('WebSocket closed');
        // Clear pending requests - their callbacks will never fire
        pendingRequests.clear();

        // Check if server is still running
        const healthy = await checkServerHealth();

        if (!healthy) {
            // Server is down - show helpful UI instead of retrying forever
            console.error('Server appears to be down');
            setConnectionState('server_down');
            return;
        }

        // Server is up but WebSocket closed - reconnect with backoff
        if (reconnectAttempts >= 10) {
            console.error('Max reconnection attempts exceeded');
            setConnectionState('server_down');
            return;
        }

        setConnectionState('disconnected');
        const delay = Math.min(1000 * Math.pow(2, reconnectAttempts++), 30000);
        console.log(`Reconnecting in ${delay}ms (attempt ${reconnectAttempts}/10)`);
        setTimeout(connect, delay);
    };

    ws.onerror = (err) => {
        console.error('WebSocket error:', err);
        // Note: onerror is always followed by onclose, which handles reconnection
    };
}

function handleMessage(msg) {
    switch (msg.type) {
        case 'connected':
            sessionId = msg.sessionId;
            console.log('Connected to session: ' + sessionId + ', model: ' + msg.model);
            // Update model picker with server's model
            modelPicker.setModelsFromServer(msg.model);
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
            // Track conversation ID for forking (only if not viewing historical)
            if (msg.conversationId && !isHistoricalView) {
                currentConversationId = msg.conversationId;
            }
            // Refresh history sidebar to show updated conversation
            historySidebar.load();
            break;

        case 'error':
            addError(msg.message || 'Unknown error');
            isStreaming = false;
            break;

        case 'stripped':
            // Handle stripMarkdown response
            const strippedCallback = pendingRequests.get(msg.requestId);
            if (strippedCallback) {
                strippedCallback(msg.text);
                pendingRequests.delete(msg.requestId);
            }
            break;

        case 'history':
            // This is the active session's history, not a historical load
            isHistoricalView = false;
            resetInputToActive();
            loadHistory(msg.messages || []);
            break;

        case 'commandResult':
            if (msg.command === 'new') {
                clearConversation();
                currentConversationId = null;  // Reset conversation ID
                showToast('New conversation started');
                // Refresh history to show the new session
                historySidebar.load();
            } else if (msg.command === 'status' && msg.data) {
                const statusData = msg.data;
                showToast('Model: ' + statusData.model + ' | Messages: ' + statusData.messages);
            } else if (msg.command === 'model') {
                if (msg.success && msg.model) {
                    modelPicker.handleModelChange(msg.model);
                    showToast('Switched to ' + msg.model);
                }
            }
            break;

        case 'conversationResumed':
            // Server has loaded the historical conversation into session
            console.log('Conversation resumed:', msg.conversationId, 'model:', msg.model);
            // Update model picker if model changed
            if (msg.model) {
                modelPicker.handleModelChange(msg.model);
            }
            break;

        case 'conversationForked':
            // Server has created a forked conversation
            console.log('Conversation forked:', msg.originalId, '->', msg.newId, 'with', msg.responseCount, 'responses');
            // Update tracked conversation IDs
            currentConversationId = msg.newId;  // This is now the active conversation
            viewedConversationId = null;  // Clear historical view ID
            // Reset historical view flags - this is now an active conversation
            isHistoricalView = false;
            resetInputToActive();
            // Update model picker if provided
            if (msg.model) {
                modelPicker.handleModelChange(msg.model);
            }
            // Truncate messages to match fork point (remove messages after fork)
            const forkMsgCount = msg.responseCount * 2;  // Each response = user + assistant
            if (messageStore.messages.length > forkMsgCount) {
                // Get truncated messages and re-render
                const truncatedMsgs = messageStore.messages.slice(0, forkMsgCount);
                loadHistory(truncatedMsgs);
            }
            showToast('Forked conversation created');
            // Refresh history to show new conversation
            historySidebar.load();
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
            scrollToBottom();
            return;
        }
    }

    // Create new assistant message
    appendMessage('assistant', msg.content, msg.messageId);
}

/**
 * Parse tool calls from assistant message content.
 * Tool calls are formatted as: **Tool Call:** `name` followed by ```json...```
 * Returns array of {type: 'text'|'toolcall', content/name/args/result}
 */
function parseToolCallsFromContent(content) {
    const parts = [];
    // Pattern: **Tool Call:** `name` + optional ```json...``` + optional **Result:** ```...```
    const toolCallRegex = /\*\*Tool Call:\*\*\s*`([^`]+)`(?:\s*```json\n?([\s\S]*?)```)?(?:\s*\*\*Result:\*\*\s*```\n?([\s\S]*?)```)?/g;

    // Use matchAll to find all tool call patterns
    const matches = Array.from(content.matchAll(toolCallRegex));

    if (matches.length === 0) {
        // No tool calls, return content as-is
        return [{ type: 'text', content: content }];
    }

    let lastIndex = 0;

    for (const match of matches) {
        // Add text before this tool call
        if (match.index > lastIndex) {
            const textBefore = content.slice(lastIndex, match.index).trim();
            if (textBefore) {
                parts.push({ type: 'text', content: textBefore });
            }
        }

        // Parse tool call
        const toolName = match[1];
        let args = null;

        if (match[2]) {
            try {
                args = JSON.parse(match[2].trim());
            } catch (e) {
                args = { raw: match[2].trim() };
            }
        }

        // Extract result from group 3 if present
        const result = match[3] ? match[3].trim() : null;

        parts.push({
            type: 'toolcall',
            name: toolName,
            args: args,
            result: result
        });

        lastIndex = match.index + match[0].length;
    }

    // Add remaining text after last tool call
    if (lastIndex < content.length) {
        const textAfter = content.slice(lastIndex).trim();
        if (textAfter) {
            parts.push({ type: 'text', content: textAfter });
        }
    }

    return parts;
}

/**
 * Render an assistant message that may contain tool calls.
 * Separates tool calls and renders them with proper UI.
 */
function appendAssistantMessageWithToolCalls(content) {
    const parts = parseToolCallsFromContent(content);

    // If no tool calls found, render as normal message
    if (parts.length === 0 || (parts.length === 1 && parts[0].type === 'text')) {
        appendMessage('assistant', content);
        return;
    }

    // Render each part appropriately
    for (const part of parts) {
        if (part.type === 'text') {
            appendMessage('assistant', part.content);
        } else if (part.type === 'toolcall') {
            const toolCallId = 'history-' + Date.now() + '-' + Math.random().toString(36).substr(2, 9);
            addToolCall(part.name, part.args, toolCallId);
            // Immediately complete since this is from history
            completeToolCall(toolCallId, part.result || '(completed)');
        }
    }
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
        // Reset temp chat for empty conversation
        tempChatToggle.reset();
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
        if (msg.role === 'assistant' && msg.content.includes('**Tool Call:**')) {
            // Parse and render tool calls properly
            appendAssistantMessageWithToolCalls(msg.content);
        } else {
            appendMessage(msg.role, msg.content);
        }
    }

    // Mark conversation as started (disables temp chat toggle for loaded conversations)
    tempChatToggle.markConversationStarted();

    // Scroll to bottom after loading history
    scrollToBottom();
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

    // Strip context tags for display, but keep original in store
    const displayContent = role === 'user' ? stripContextTags(content) : content;

    const contentDiv = document.createElement('div');
    contentDiv.className = 'message-content';
    contentDiv.innerHTML = safeMarkdown(displayContent);
    container.appendChild(contentDiv);

    // Store original content (with context) for model continuation
    const msgId = messageStore.add(role, content, container);

    const actions = createActionIcons(role, msgId, displayContent);
    container.appendChild(actions);

    if (role === 'assistant' && streamingId) {
        container.id = 'current-message';
        currentMessageId = streamingId;
        isStreaming = true;
    }

    conversation.appendChild(container);
    applyCodeBlockEnhancements(contentDiv);
    updateLastMessageIndicators();
    scrollToBottom();
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
    scrollToBottom();
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
    scrollToBottom();
}

function addError(message) {
    const conversation = document.getElementById('conversation');
    const errorDiv = document.createElement('div');
    errorDiv.className = 'message error';
    errorDiv.textContent = message;
    conversation.appendChild(errorDiv);
    scrollToBottom();
}

function clearConversation() {
    const conversation = document.getElementById('conversation');
    conversation.innerHTML = `
        <div class="empty-state" id="empty-state">
            <h2>How can I help you today?</h2>
            <p>Type a question below and press Enter to send.</p>
        </div>
    `;
    // Clear historical view flags (this is now an active session)
    isHistoricalView = false;
    viewedConversationId = null;
    resetInputToActive();
    currentMessageId = null;
    messageStore.clear();
    // Reset temp chat toggle for new conversation
    tempChatToggle.reset();
}

// ============================================================================
// Code Block Enhancements
// ============================================================================

function applyCodeBlockEnhancements(container) {
    // Render mermaid diagrams first
    container.querySelectorAll('pre code.language-mermaid').forEach(async (block) => {
        const pre = block.parentElement;
        if (pre.parentElement.classList.contains('mermaid-wrapper')) return;

        const wrapper = document.createElement('div');
        wrapper.className = 'mermaid-wrapper';
        pre.parentNode.insertBefore(wrapper, pre);

        const mermaidDiv = document.createElement('div');
        mermaidDiv.className = 'mermaid';
        wrapper.appendChild(mermaidDiv);

        // Store original code for copy button
        const originalCode = block.textContent;

        try {
            const { svg } = await mermaid.render('mermaid-' + Date.now() + '-' + Math.random().toString(36).substr(2, 9), originalCode);
            mermaidDiv.innerHTML = svg;
            pre.remove();

            // Add copy button for original mermaid code
            const copyBtn = document.createElement('button');
            copyBtn.className = 'copy-btn';
            copyBtn.innerHTML = ICONS.copy + '<span>Copy</span>';
            copyBtn.onclick = function() {
                navigator.clipboard.writeText(originalCode).then(() => {
                    copyBtn.innerHTML = ICONS.copy + '<span>Copied!</span>';
                    setTimeout(() => {
                        copyBtn.innerHTML = ICONS.copy + '<span>Copy</span>';
                    }, 1500);
                }).catch(err => {
                    console.error('Clipboard write failed:', err);
                    showToast('Failed to copy to clipboard');
                });
            };
            wrapper.appendChild(copyBtn);
        } catch (err) {
            console.error('Mermaid render error:', err);
            // Keep original code block on error, apply normal enhancements
            if (wrapper && wrapper.parentNode) {
                wrapper.remove();
            }
        }
    });

    // Handle regular code blocks
    container.querySelectorAll('pre').forEach(pre => {
        // Skip mermaid blocks (handled above)
        const code = pre.querySelector('code');
        if (code && code.classList.contains('language-mermaid')) return;

        if (!pre.parentElement.classList.contains('code-wrapper')) {
            const wrapper = document.createElement('div');
            wrapper.className = 'code-wrapper';
            pre.parentNode.insertBefore(wrapper, pre);
            wrapper.appendChild(pre);

            const copyBtn = document.createElement('button');
            copyBtn.className = 'copy-btn';
            copyBtn.innerHTML = ICONS.copy + '<span>Copy</span>';
            copyBtn.onclick = function() {
                const codeEl = pre.querySelector('code') || pre;
                navigator.clipboard.writeText(codeEl.textContent).then(() => {
                    copyBtn.innerHTML = ICONS.copy + '<span>Copied!</span>';
                    setTimeout(() => {
                        copyBtn.innerHTML = ICONS.copy + '<span>Copy</span>';
                    }, 1500);
                }).catch(err => {
                    console.error('Clipboard write failed:', err);
                    showToast('Failed to copy to clipboard');
                });
            };
            wrapper.appendChild(copyBtn);
        }
    });

    container.querySelectorAll('pre code').forEach(block => {
        // Skip mermaid blocks
        if (block.classList.contains('language-mermaid')) return;
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

function createActionIcons(role, messageId, content) {
    const container = document.createElement('div');
    container.className = 'message-actions';

    // Check if there's actual content (not empty/whitespace-only)
    const hasContent = content && content.trim().length > 0;

    // Copy buttons only for messages with content
    if (hasContent) {
        // Copy markdown
        container.appendChild(createIconButton('copy', 'Copy markdown', () => {
            const msg = messageStore.messages.find(m => m.id === messageId);
            if (msg) {
                navigator.clipboard.writeText(msg.content).then(() => {
                    showToast('Copied to clipboard');
                }).catch(err => {
                    console.error('Clipboard write failed:', err);
                    showToast('Failed to copy to clipboard');
                });
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
    }

    if (role === 'user') {
        const editBtn = createIconButton('edit', 'Edit', () => showInlineEditor(messageId));
        editBtn.classList.add('action-edit');
        container.appendChild(editBtn);
    } else if (role === 'assistant') {
        // Only show regenerate button if there's actual content
        if (hasContent) {
            const regenBtn = createIconButton('regenerate', 'Regenerate', () => regenerateResponse(messageId));
            regenBtn.classList.add('action-regenerate');
            container.appendChild(regenBtn);

            // Fork button - only visible for GUI-originated historical conversations
            const forkBtn = createIconButton('fork', 'Fork from here', () => forkAtMessage(messageId));
            forkBtn.classList.add('action-fork');
            container.appendChild(forkBtn);
        }
    }

    return container;
}

// Strip markdown formatting from text (client-side)
function stripMarkdown(text) {
    if (!text) return '';

    // Remove code blocks (preserve content)
    text = text.replace(/```[\s\S]*?```/g, match => {
        const lines = match.split('\n');
        // Remove first line (```lang) and last line (```)
        return lines.slice(1, -1).join('\n');
    });

    // Remove inline code
    text = text.replace(/`([^`]+)`/g, '$1');

    // Remove bold/italic
    text = text.replace(/\*\*\*(.+?)\*\*\*/g, '$1');
    text = text.replace(/\*\*(.+?)\*\*/g, '$1');
    text = text.replace(/\*(.+?)\*/g, '$1');
    text = text.replace(/___(.+?)___/g, '$1');
    text = text.replace(/__(.+?)__/g, '$1');
    text = text.replace(/_(.+?)_/g, '$1');

    // Remove headers
    text = text.replace(/^#{1,6}\s+/gm, '');

    // Remove blockquotes
    text = text.replace(/^>\s+/gm, '');

    // Remove horizontal rules
    text = text.replace(/^[-*_]{3,}\s*$/gm, '');

    // Remove links but keep text
    text = text.replace(/\[([^\]]+)\]\([^)]+\)/g, '$1');

    // Remove images
    text = text.replace(/!\[([^\]]*)\]\([^)]+\)/g, '$1');

    // Remove strikethrough
    text = text.replace(/~~(.+?)~~/g, '$1');

    // Clean up extra whitespace
    text = text.replace(/\n{3,}/g, '\n\n');

    return text.trim();
}

// Copy plain text (strip markdown client-side for immediate clipboard access)
function copyPlainText(content) {
    const stripped = stripMarkdown(content);

    navigator.clipboard.writeText(stripped).then(() => {
        showToast('Copied plain text');
    }).catch(err => {
        console.error('Clipboard write failed:', err);
        // Fallback for older browsers or permission issues
        try {
            const textarea = document.createElement('textarea');
            textarea.value = stripped;
            textarea.style.position = 'fixed';
            textarea.style.opacity = '0';
            document.body.appendChild(textarea);
            textarea.select();
            document.execCommand('copy');
            document.body.removeChild(textarea);
            showToast('Copied plain text');
        } catch (e) {
            showToast('Failed to copy to clipboard');
        }
    });
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

    if (!isConnected()) {
        showToast('Not connected to server');
        return;
    }
    safeSend({
        type: 'edit',
        keepTurns: turnCount,
        newContent: newContent
    });
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

    if (!isConnected()) {
        showToast('Not connected to server');
        return;
    }

    messageStore.removeMessage(messageId);
    updateLastMessageIndicators();

    safeSend({
        type: 'regenerate',
        userContent: userMsg.content
    });
}

/**
 * Fork conversation at a specific message.
 * Creates a new conversation by cloning everything up to this message.
 * Only available for GUI-originated conversations.
 */
function forkAtMessage(messageId) {
    const msg = messageStore.messages.find(m => m.id === messageId);
    if (!msg) return;

    // Get the message index (this is the fork point - include up to this message)
    const msgIndex = messageStore.messages.findIndex(m => m.id === messageId);
    if (msgIndex < 0) return;

    // Get conversation ID from either historical view or current session
    const convId = viewedConversationId || currentConversationId;
    if (!convId) {
        showToast('Cannot fork: no conversation available');
        return;
    }

    if (!isConnected()) {
        showToast('Not connected to server');
        return;
    }

    // Convert UI message index to response index
    // In the UI, we have alternating user/assistant messages
    // In the database, each "response" is a user prompt + assistant response
    // UI index 0 (user) + 1 (assistant) = response index 0
    // UI index 2 (user) + 3 (assistant) = response index 1
    // So response index = floor(msgIndex / 2)
    const responseIndex = Math.floor(msgIndex / 2);

    showToast('Creating fork...');

    safeSend({
        type: 'forkConversation',
        conversationId: convId,
        forkAtIndex: responseIndex
    });
}

// ============================================================================
// Input Handling
// ============================================================================

function sendMessage() {
    const input = document.getElementById('input');
    const query = input.value.trim();

    if (!query || isStreaming) return;

    if (!isConnected()) {
        showToast('Not connected to server');
        return;
    }

    // Mark conversation as started (disables temp chat toggle)
    tempChatToggle.markConversationStarted();

    // Add user message to UI
    appendMessage('user', query);

    // Collect any pending images from drag-drop and attachment panel
    const images = window.pendingImages || [];
    window.pendingImages = [];
    attachmentPanel.clear();

    // Build query message
    const queryMsg = {
        type: 'query',
        query: query,
        mode: 'assistant',
        images: images
    };

    // Include noLog flag if temp chat is enabled
    if (tempChatToggle.isEnabled()) {
        queryMsg.noLog = true;
    }

    // Send via WebSocket
    safeSend(queryMsg);

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
    if (!toast) {
        console.warn('Toast element not found');
        return;
    }
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

    // Clamp selectedActionIndex to valid range for filtered list
    if (filtered.length > 0) {
        selectedActionIndex = Math.min(selectedActionIndex, filtered.length - 1);
    } else {
        selectedActionIndex = 0;
    }

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
        navigator.clipboard.writeText(lastAssistant.content).then(() => {
            showToast('Copied last response');
        }).catch(err => {
            console.error('Clipboard write failed:', err);
            showToast('Failed to copy to clipboard');
        });
    }
}

function copyAllMessages() {
    const text = messageStore.messages.map(m => {
        // Strip context from user messages for cleaner copy
        const content = m.role === 'user' ? stripContextTags(m.content) : m.content;
        return `${m.role === 'user' ? 'You' : 'Assistant'}: ${content}`;
    }).join('\n\n');
    navigator.clipboard.writeText(text).then(() => {
        showToast('Copied conversation');
    }).catch(err => {
        console.error('Clipboard write failed:', err);
        showToast('Failed to copy to clipboard');
    });
}

function startNewSession() {
    safeSend({ type: 'command', command: 'new' });
}

function showStatus() {
    safeSend({ type: 'command', command: 'status' });
}

// ============================================================================
// Event Listeners
// ============================================================================

document.addEventListener('DOMContentLoaded', function() {
    const input = document.getElementById('input');
    const sendBtn = document.getElementById('send-btn');
    const actionSearch = document.getElementById('action-search');
    const actionBackdrop = document.querySelector('.action-panel-backdrop');

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

        // Send message with Enter (Shift+Enter for newline)
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });
    addReadlineShortcuts(input, autoResizeInput);

    // Send button
    sendBtn.addEventListener('click', sendMessage);

    // Sidebar toggle button (header bar)
    const sidebarToggle = document.getElementById('sidebar-toggle');
    if (sidebarToggle) {
        sidebarToggle.addEventListener('click', function() {
            historySidebar.toggle();
        });
    }

    // Model picker
    modelPicker.init();

    // Temporary chat toggle
    tempChatToggle.init();

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
    const historySearch = document.getElementById('history-search');
    if (historySearch) {
        let searchTimeout = null;
        historySearch.addEventListener('input', function(e) {
            clearTimeout(searchTimeout);
            searchTimeout = setTimeout(function() {
                historySidebar.search(e.target.value);
            }, 300);
        });
    }

    // @ button
    const atBtn = document.getElementById('at-btn');
    if (atBtn) {
        atBtn.addEventListener('click', function() {
            input.value += '@';
            input.focus();
            atAutocomplete.handleInput(input);
        });
    }

    // Capture button and dropdown
    const captureBtn = document.getElementById('capture-btn');
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

    // RAG create collection button
    const ragCreateBtn = document.getElementById('rag-create-btn');
    const ragNewName = document.getElementById('rag-new-name');
    if (ragCreateBtn && ragNewName) {
        ragCreateBtn.addEventListener('click', function() {
            ragPanel.createCollection(ragNewName.value);
            ragNewName.value = '';
        });
        ragNewName.addEventListener('keydown', function(e) {
            if (e.key === 'Enter') {
                ragPanel.createCollection(ragNewName.value);
                ragNewName.value = '';
            }
        });
    }

    // RAG add button
    const ragAddBtn = document.getElementById('rag-add-btn');
    const ragAddInput = document.getElementById('rag-add-input');
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

    // RAG show sources checkbox
    const ragShowSourcesCheckbox = document.getElementById('rag-show-sources-checkbox');
    if (ragShowSourcesCheckbox) {
        ragShowSourcesCheckbox.addEventListener('change', function(e) {
            ragPanel.updateSources(e.target.checked);
        });
    }

    // Attachment panel button
    const attachmentBtn = document.getElementById('attachment-btn');
    if (attachmentBtn) {
        attachmentBtn.addEventListener('click', function(e) {
            e.stopPropagation();
            attachmentPanel.toggle();
        });
    }

    // New conversation buttons (header and input controls)
    const newConvBtnHeader = document.getElementById('new-conversation-btn-header');
    if (newConvBtnHeader) {
        newConvBtnHeader.addEventListener('click', function() {
            startNewSession();
        });
    }

    const newConvBtnInput = document.getElementById('new-conversation-btn-input');
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
        // Close attachment panel
        if (!e.target.closest('#attachment-wrapper')) {
            attachmentPanel.hide();
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
            const list = document.getElementById('action-list');
            const items = list.querySelectorAll('.action-item');

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
                const selected = items[selectedActionIndex];
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
