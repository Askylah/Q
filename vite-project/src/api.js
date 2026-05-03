// api.js
// Handles all communication with the FastAPI backend on port 8000 using Universal API Client principles

class PersonaAPI {
    constructor(baseUrl = 'http://127.0.0.1:8000') {
        this.baseUrl = baseUrl;
    }

    /**
     * Internal abstraction for all non-streaming HTTP requests.
     * Centralizes error handling, timeouts, and JSON parsing.
     */
    async _fetch(endpoint, options = {}, retries = 1) {
        const url = `${this.baseUrl}${endpoint}`;

        // Timeout AbortController
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 30000); // 30s timeout

        const finalOptions = {
            ...options,
            signal: controller.signal,
            headers: {
                'Content-Type': 'application/json',
                ...options.headers,
            }
        };

        try {
            const response = await fetch(url, finalOptions);
            clearTimeout(timeoutId);

            if (!response.ok) {
                let errorText = await response.text();
                try {
                    const errorJson = JSON.parse(errorText);
                    errorText = errorJson.detail || errorText;
                } catch (e) { /* keep raw text */ }
                throw new Error(`[HTTP ${response.status}] ${errorText}`);
            }

            return await response.json();
        } catch (error) {
            clearTimeout(timeoutId);

            // Handle retries on network failures or timeouts
            if (retries > 0 && (error.name === 'AbortError' || error.message.includes('fetch'))) {
                console.warn(`Retrying request to ${endpoint}... (${retries} attempts left)`);
                return this._fetch(endpoint, options, retries - 1);
            }

            console.error(`API Error on ${endpoint}:`, error);
            throw error;
        }
    }

    async fetchPersonas(username = "default_user") {
        try {
            return await this._fetch(`/personas?username=${username}`);
        } catch (e) {
            return null;
        }
    }

    async fetchChatHistory(personaKey, username = "default_user", limit = 50) {
        try {
            const data = await this._fetch(`/chat/${personaKey}?username=${username}&limit=${limit}`);
            return data.history || [];
        } catch (e) {
            return [];
        }
    }

    async clearChatHistory(personaKey, username = "default_user") {
        try {
            await this._fetch(`/chat/${personaKey}/clear?username=${username}`, { method: 'POST' });
            return true;
        } catch (e) {
            return false;
        }
    }

    async wipePersonaMemories(personaKey, username = "default_user") {
        try {
            await this._fetch(`/personas/${personaKey}/wipe?username=${username}`, { method: 'POST' });
            return true;
        } catch (e) {
            return false;
        }
    }

    async deletePersona(personaKey, username = "default_user") {
        try {
            await this._fetch(`/personas/${personaKey}?username=${username}`, { method: 'DELETE' });
            return true;
        } catch (e) {
            return false;
        }
    }

    async createPersona(payload) {
        return await this._fetch(`/personas`, {
            method: 'POST',
            body: JSON.stringify(payload)
        });
    }

    // --- LORE (Knowledge Graph) ---
    async fetchLoreEntries(personaKey, username = "default_user") {
        try {
            const data = await this._fetch(`/personas/${personaKey}/lore?username=${username}`);
            return data.entries || [];
        } catch (e) {
            return [];
        }
    }

    async createLoreEntry(personaKey, username, title, content, apiKeys = {}) {
        return await this._fetch(`/personas/${personaKey}/lore`, {
            method: 'POST',
            body: JSON.stringify({ username, title, content, active_api_keys: apiKeys })
        });
    }

    async updateLoreEntry(personaKey, entryId, username, title, content, apiKeys = {}) {
        return await this._fetch(`/personas/${personaKey}/lore/${entryId}`, {
            method: 'PUT',
            body: JSON.stringify({ username, title, content, active_api_keys: apiKeys })
        });
    }

    async deleteLoreEntry(personaKey, entryId, username = "default_user") {
        try {
            await this._fetch(`/personas/${personaKey}/lore/${entryId}?username=${username}`, { method: 'DELETE' });
            return true;
        } catch (e) {
            return false;
        }
    }

    async uploadFile(file) {
        const formData = new FormData();
        formData.append("file", file);

        const response = await fetch(`${this.baseUrl}/upload`, {
            method: 'POST',
            body: formData
        });

        if (!response.ok) {
            throw new Error(`Upload failed: ${response.statusText}`);
        }
        return await response.json();
    }

    async saveChatMessage(personaKey, username, role, content) {
        return await this._fetch(`/chat/${personaKey}`, {
            method: 'POST',
            body: JSON.stringify({
                username,
                role,
                content
            })
        });
    }

    async deleteChatMessage(messageId) {
        try {
            await this._fetch(`/chat/message/${messageId}`, { method: 'DELETE' });
            return true;
        } catch (e) {
            return false;
        }
    }

    /**
     * Dedicated method for Server-Sent Events (SSE).
     */
    async streamChatComplete({
        personaKey,
        message,
        username = "default_user",
        chatHistory = [],
        modelId = "openrouter/auto",
        expertModelId = "google/gemini-3.1-pro-preview",
        temperature = 0.9,
        topP = 1.0,
        topK = 0,
        presencePenalty = 0.0,
        frequencyPenalty = 0.0,
        thinkingLevel = "Off",
        customBaseUrl = "",
        customProviderType = "openai",
        customAuthHeaderName = "Authorization",
        customAuthPrefix = "Bearer ",
        bypass_firewall = false,
        apiKeys = {},
        workspaceContext = null,
        abortController,
        onChunk,
        onDone,
        onError
    }) {
        let fullStreamedContent = '';
        try {
            const fetchOptions = {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Accept': 'text/event-stream'
                },
                body: JSON.stringify({
                    username,
                    message,
                    chat_history: chatHistory,
                    target_model_id: modelId,
                    expert_model_id: expertModelId,
                    temperature,
                    top_p: topP,
                    top_k: topK,
                    presence_penalty: presencePenalty,
                    frequency_penalty: frequencyPenalty,
                    thinking_level: thinkingLevel,
                    bypass_firewall: bypass_firewall,
                    custom_base_url: customBaseUrl,
                    custom_provider_type: customProviderType,
                    custom_auth_header_name: customAuthHeaderName,
                    custom_auth_prefix: customAuthPrefix,
                    active_api_keys: apiKeys,
                    workspace_context: workspaceContext
                })
            };

            if (abortController) {
                fetchOptions.signal = abortController.signal;
            }

            const response = await fetch(`${this.baseUrl}/chat/${personaKey}/stream`, fetchOptions);

            if (!response.ok) {
                let errText = await response.text();
                throw new Error(errText);
            }

            const reader = response.body.getReader();
            const decoder = new TextDecoder('utf-8');
            let buffer = '';
            // fullStreamedContent hoisted above try block

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;

                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split('\n');
                buffer = lines.pop() || '';

                for (const line of lines) {
                    const trimmedLine = line.trim();
                    if (!trimmedLine) continue;

                    if (trimmedLine === 'data: [DONE]') {
                        if (onDone) onDone(fullStreamedContent);
                        return;
                    }

                    if (trimmedLine.startsWith('data: ')) {
                        try {
                            const dataStr = trimmedLine.substring(6);
                            if (dataStr) {
                                const parsed = JSON.parse(dataStr);

                                // Intercept custom control blocks
                                if (parsed.control) {
                                    if (parsed.control === 'reflection_started' && onChunk) {
                                        // We pass a special object representing the control event
                                        onChunk({ type: 'control', event: 'reflection_started' });
                                    }
                                    continue;
                                }

                                if (parsed.choices && parsed.choices[0] && parsed.choices[0].delta) {
                                    const contentChunk = parsed.choices[0].delta.content;
                                    if (contentChunk) {
                                        fullStreamedContent += contentChunk;
                                        if (onChunk) onChunk(contentChunk);
                                    }
                                }
                            }
                        } catch (e) {
                            console.warn("Failed to parse SSE line:", line, e);
                        }
                    }
                }
            }

            if (buffer.trim() === 'data: [DONE]') {
                if (onDone) onDone(fullStreamedContent);
            }

        } catch (error) {
            if (error.name === 'AbortError') {
                console.log("Stream forcefully aborted by user.");
                // Immediately finish stream and save progress
                if (onDone && typeof fullStreamedContent !== 'undefined') {
                    onDone(fullStreamedContent);
                }
                return;
            }
            console.error("Streaming error:", error);
            if (onError) onError(error.message || "Connection failed");
        }
    }

    async fetchFileTree(path = ".") {
        return this._fetch(`/workspace/tree?path=${encodeURIComponent(path)}`);
    }

    async fetchFileContent(path) {
        const res = await this._fetch(`/workspace/file?path=${encodeURIComponent(path)}`);
        return res.content;
    }

    async saveFileContent(path, content) {
        return this._fetch('/workspace/save', {
            method: 'POST',
            body: JSON.stringify({ path, content })
        });
    }

    async createItem(path, item_type = "file") {
        return this._fetch('/workspace/create', {
            method: 'POST',
            body: JSON.stringify({ path, item_type })
        });
    }

    async deleteItem(path) {
        return this._fetch(`/workspace/delete?path=${encodeURIComponent(path)}`, {
            method: 'DELETE'
        });
    }

    // --- GROUP CHAT ---
    async fetchUserGroupSessions(username = "default_user") {
        try {
            const data = await this._fetch(`/groupchats/sessions/${encodeURIComponent(username)}`);
            return data.sessions || [];
        } catch (e) {
            return [];
        }
    }

    async fetchGroupHistory(sessionId, username = "default_user", limit = 100) {
        try {
            const data = await this._fetch(`/groupchat/${encodeURIComponent(sessionId)}?username=${username}&limit=${limit}`);
            return data.history || [];
        } catch (e) {
            return [];
        }
    }

    async saveGroupMessage(sessionId, { username, role, persona_key, persona_name, persona_avatar, content, is_observer = false }) {
        try {
            await this._fetch(`/groupchat/${encodeURIComponent(sessionId)}`, {
                method: 'POST',
                body: JSON.stringify({ username, role, persona_key, persona_name, persona_avatar, content, is_observer })
            });
            return true;
        } catch (e) {
            return false;
        }
    }

    async clearGroupHistory(sessionId, username = "default_user") {
        try {
            await this._fetch(`/groupchat/${encodeURIComponent(sessionId)}/clear?username=${username}`, { method: 'POST' });
            return true;
        } catch (e) {
            return false;
        }
    }

    async deleteGroupChatMessage(messageId) {
        try {
            await this._fetch(`/groupchat/message/${messageId}`, { method: 'DELETE' });
            return true;
        } catch (e) {
            return false;
        }
    }

    // --- SETTINGS & GOVERNANCE ---
    async fetchUserSettings(username = "default_user") {
        try {
            return await this._fetch(`/settings/${encodeURIComponent(username)}`);
        } catch (e) {
            return null;
        }
    }

    async updateUserSettings(username, settings) {
        try {
            return await this._fetch(`/settings`, {
                method: 'POST',
                body: JSON.stringify({ username, ...settings })
            });
        } catch (e) {
            return null;
        }
    }
}

// Export a singleton instance globally for the App to use
export const api = new PersonaAPI();
