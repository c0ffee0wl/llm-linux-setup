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
if (typeof marked === 'undefined' || typeof hljs === 'undefined') {
    document.body.innerHTML = '<div style="padding: 20px; color: #c00;">' +
        '<h3>JavaScript assets failed to load</h3>' +
        '<p>Missing: ' +
        (typeof marked === 'undefined' ? 'marked.js ' : '') +
        (typeof hljs === 'undefined' ? 'highlight.js' : '') +
        '</p>' +
        '<p>Run: <code>./install-llm-tools.sh</code> to download assets.</p>' +
        '</div>';
    throw new Error('Required JavaScript libraries not loaded');
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

// Pending requests for async responses (e.g., stripMarkdown)
const pendingRequests = new Map();

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
    branch: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="6" y1="3" x2="6" y2="15"/><circle cx="18" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M18 9a9 9 0 01-9 9"/></svg>'
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
            console.log(`Connected to session: ${sessionId}, model: ${msg.model}`);
            break;

        case 'text':
            handleTextMessage(msg);
            break;

        case 'tool_start':
            addToolStatus(`Running ${msg.tool}...`);
            break;

        case 'tool_done':
            completeToolStatus();
            break;

        case 'done':
            finalizeMessage();
            isStreaming = false;
            break;

        case 'error':
            addError(msg.message || 'Unknown error');
            isStreaming = false;
            break;

        case 'stripped':
            // Handle stripMarkdown response
            const callback = pendingRequests.get(msg.requestId);
            if (callback) {
                callback(msg.text);
                pendingRequests.delete(msg.requestId);
            }
            break;

        case 'history':
            loadHistory(msg.messages || []);
            break;

        case 'branched':
            showToast(`Branched to new session: ${msg.newSessionId}`);
            break;

        case 'commandResult':
            if (msg.command === 'new') {
                clearConversation();
                showToast('New conversation started');
            } else if (msg.command === 'status' && msg.data) {
                const status = msg.data;
                showToast(`Model: ${status.model} | Messages: ${status.messages}`);
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
            contentDiv.innerHTML = marked.parse(msg.content);
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
    contentDiv.innerHTML = marked.parse(content);
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

function addToolStatus(message) {
    const conversation = document.getElementById('conversation');
    const statusDiv = document.createElement('div');
    statusDiv.className = 'message tool-status running';
    statusDiv.id = 'current-tool-status';
    statusDiv.textContent = message;

    // Append to end - server sends events in correct order, so this maintains order
    // Tool statuses always appear after the current streaming message because
    // the streaming message was appended first
    conversation.appendChild(statusDiv);
    window.scrollTo(0, document.body.scrollHeight);
}

function completeToolStatus() {
    const current = document.getElementById('current-tool-status');
    if (current) {
        current.classList.remove('running');
        current.classList.add('completed');
        current.removeAttribute('id');
    }
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
    contentDiv.innerHTML = marked.parse(newContent);
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

document.addEventListener('DOMContentLoaded', () => {
    const input = document.getElementById('input');
    const sendBtn = document.getElementById('send-btn');
    const actionSearch = document.getElementById('action-search');
    const actionBackdrop = document.querySelector('.action-panel-backdrop');

    // Input handling
    input.addEventListener('input', autoResizeInput);
    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && e.ctrlKey) {
            e.preventDefault();
            sendMessage();
        }
    });
    addReadlineShortcuts(input, autoResizeInput);

    // Send button
    sendBtn.addEventListener('click', sendMessage);

    // Global keyboard shortcuts
    document.addEventListener('keydown', (e) => {
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
        }
    });

    // Action panel search
    actionSearch?.addEventListener('input', (e) => {
        selectedActionIndex = 0;
        renderActionList(e.target.value);
    });

    actionSearch?.addEventListener('keydown', (e) => {
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

    // Action panel backdrop click
    actionBackdrop?.addEventListener('click', hideActionPanel);

    // Connect WebSocket
    connect();
});
