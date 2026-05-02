import { useState, useEffect, useRef } from 'react'
import './App.css'
import { api } from './api'
import Editor from '@monaco-editor/react'

function App() {
  const [personas, setPersonas] = useState({});
  const [activePersona, setActivePersona] = useState(null);
  const [isSidebarOpen, setIsSidebarOpen] = useState(true);

  const [chatHistory, setChatHistory] = useState([]);
  const [currentInput, setCurrentInput] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const [isReflecting, setIsReflecting] = useState(false);
  const [liveStreamText, setLiveStreamText] = useState("");
  const [showSettings, setShowSettings] = useState(false);
  const [settingsSection, setSettingsSection] = useState('api');
  const [currentView, setCurrentView] = useState("chat");
  const [globalMode, setGlobalMode] = useState("roleplay");
  const [workspaceCode, setWorkspaceCode] = useState("// Initialization complete.\n// Select a file or wait for an agent to stream code...");
  const [editorLanguage, setEditorLanguage] = useState("python");
  const [fileTree, setFileTree] = useState(null);
  const [activeFilePath, setActiveFilePath] = useState("");
  const [workspacePath, setWorkspacePath] = useState(".");
  const [expandedPaths, setExpandedPaths] = useState(new Set(['.']));
  const [isDraggingOver, setIsDraggingOver] = useState(false);

  // --- PANEL RESIZE STATE ---
  const [sidebarWidth, setSidebarWidth] = useState(() => Number(localStorage.getItem('q_sidebar_w')) || 280);
  const [fileTreeWidth, setFileTreeWidth] = useState(() => Number(localStorage.getItem('q_filetree_w')) || 220);
  const [chatPanelWidth, setChatPanelWidth] = useState(() => Number(localStorage.getItem('q_chat_w')) || 360);
  const resizingRef = useRef(null);

  // --- USER PROFILE ---
  const [userAvatar, setUserAvatar] = useState(() => localStorage.getItem('q_user_avatar') || '👩‍💻');

  // --- GROUP CHAT STATE ---
  const [groupMembers, setGroupMembers] = useState([]);      // persona keys
  const [groupObserver, setGroupObserver] = useState(null);  // persona key or null
  const [groupHistory, setGroupHistory] = useState([]);      // shared thread
  const [groupTurnLimit, setGroupTurnLimit] = useState(2);   // 1-4
  const [groupStreaming, setGroupStreaming] = useState(false);
  const [groupLiveSlot, setGroupLiveSlot] = useState(null);
  const [groupSessionStarted, setGroupSessionStarted] = useState(false);
  const [groupMemberModels, setGroupMemberModels] = useState({}); // { personaKey: modelId }
  const [groupSessionName, setGroupSessionName] = useState("main");
  const [allGroupSessions, setAllGroupSessions] = useState([]);
  const groupAbortRef = useRef(null);
  const groupEndRef = useRef(null);
  const personaSnapshotsRef = useRef({}); // snapshot of individual histories before group contamination

  // --- THEME STATE ---
  const [appTheme, setAppTheme] = useState(() => {
    return localStorage.getItem('q_theme') || 'void';
  });

  // Apply theme attribute to <html> on mount and change
  useEffect(() => {
    const themeAttr = appTheme === 'void' ? '' : appTheme === 'q-dark' ? 'q-dark' : 'q-light';
    document.documentElement.setAttribute('data-theme', themeAttr);
    localStorage.setItem('q_theme', appTheme);
  }, [appTheme]);

  const [newPersona, setNewPersona] = useState({
    originalKey: "",
    key: "",
    name: "",
    avatar: "🤖",
    tagline: "",
    system_prompt: "",
    on_demand_files: [],
    access_code: "",
    om_enabled: true,
    om_turn_threshold: 5,
    deep_memory_enabled: false
  });

  const [chatAttachments, setChatAttachments] = useState([]);

  // --- LOREBOOK STATE ---
  const [loreEntries, setLoreEntries] = useState([]);
  const [loreForm, setLoreForm] = useState({ title: '', content: '' });
  const [loreEditId, setLoreEditId] = useState(null);
  const [loreLoading, setLoreLoading] = useState(false);
  const [loreSaving, setLoreSaving] = useState(false);

  // --- WORKSHOP ADVANCED SETTINGS ---
  const [apiKeys, setApiKeys] = useState(() => {
    const saved = localStorage.getItem('persona_api_keys');
    return saved ? JSON.parse(saved) : { universal: "" };
  });

  const [advancedOptions, setAdvancedOptions] = useState(() => {
    const saved = localStorage.getItem('persona_advanced_options');
    if (saved) {
      try {
        const parsed = JSON.parse(saved);
        return {
          temperature: parsed.temperature ?? 0.9,
          topP: parsed.topP ?? 1.0,
          topK: parsed.topK ?? 0,
          maxTokens: parsed.maxTokens ?? 4096,
          presencePenalty: parsed.presencePenalty ?? 0.0,
          frequencyPenalty: parsed.frequencyPenalty ?? 0.0,
          thinkingLevel: parsed.thinkingLevel ?? "Off",
          baseModelId: parsed.baseModelId ?? parsed.modelId ?? "google/gemini-3-flash-preview",
          expertModelId: parsed.expertModelId ?? "google/gemini-3.1-pro-preview",
          customBaseUrl: parsed.customBaseUrl ?? "",
          customProviderType: parsed.customProviderType ?? "openai",
          customAuthHeaderName: parsed.customAuthHeaderName ?? "Authorization",
          customAuthPrefix: parsed.customAuthPrefix ?? "Bearer ",
          bypassFirewall: parsed.bypassFirewall ?? false
        };
      } catch (e) {
        // Fallback below
      }
    }
    return {
      temperature: 0.9,
      topP: 1.0,
      topK: 0,
      maxTokens: 4096,
      presencePenalty: 0.0,
      frequencyPenalty: 0.0,
      thinkingLevel: "Off",
      baseModelId: "google/gemini-3-flash-preview",
      expertModelId: "google/gemini-3.1-pro-preview",
      customBaseUrl: "",
      customProviderType: "openai",
      customAuthHeaderName: "Authorization",
      customAuthPrefix: "Bearer ",
      bypassFirewall: false
    };
  });

  // Persist State to localStorage on change
  useEffect(() => {
    localStorage.setItem('persona_api_keys', JSON.stringify(apiKeys));
  }, [apiKeys]);

  useEffect(() => {
    localStorage.setItem('persona_advanced_options', JSON.stringify(advancedOptions));
  }, [advancedOptions]);

  const chatEndRef = useRef(null);
  const abortControllerRef = useRef(null);

  // Auto-scroll group chat to bottom
  useEffect(() => {
    setTimeout(() => {
      groupEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }, 10);
  }, [groupHistory, groupLiveSlot, currentView]);

  // Auto-scroll individual chat to bottom
  useEffect(() => {
    setTimeout(() => {
      chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }, 10);
  }, [chatHistory, liveStreamText, currentView]);

  const getGroupSessionId = () => {
    return [...groupMembers].sort().join('_') + '_' + USERNAME + '_' + (groupSessionName.trim().toLowerCase() || 'main');
  };

  // ─── GROUP CHAT ORCHESTRATION ─────────────────────────────────────────────
  const runGroupRound = async (userMessage) => {
    if (groupMembers.length === 0) return;
    const controller = new AbortController();
    groupAbortRef.current = controller;
    setGroupStreaming(true);

    const sessionId = getGroupSessionId();

    const userMsg = { role: 'user', persona_key: 'user', persona_name: USERNAME, persona_avatar: userAvatar, content: userMessage, is_observer: false };
    setGroupHistory(prev => [...prev, userMsg]);
    await api.saveGroupMessage(sessionId, { username: USERNAME, ...userMsg });

    const activeMemberKeys = groupMembers.filter(k => k !== groupObserver);
    const memberNames = activeMemberKeys.map(k => personas[k]?.name || k).join(', ');

    let thread = [...groupHistory, userMsg];

    for (let turn = 0; turn < groupTurnLimit; turn++) {
      for (const pKey of activeMemberKeys) {
        if (controller.signal.aborted) break;
        const p = personas[pKey];
        if (!p) continue;

        const groupCtx = `[Group chat context: You are in a group conversation with ${memberNames}. Stay in character. Keep replies concise and natural.]`;

        const chatHistoryForPersona = thread.map(m => ({
          role: m.persona_key === pKey ? 'assistant' : 'user',
          content: m.role === 'user' ? m.content : `[${m.persona_name}]: ${m.content}`
        }));

        let accumulated = '';
        setGroupLiveSlot({ personaKey: pKey, name: p.name, avatar: p.avatar, text: '' });

        await new Promise((resolve) => {
          api.streamChatComplete({
            personaKey: pKey,
            message: groupCtx,
            username: USERNAME,
            chatHistory: chatHistoryForPersona,
            modelId: groupMemberModels[pKey] || advancedOptions.baseModelId || 'openrouter/auto',
            apiKeys,
            bypass_firewall: advancedOptions.bypassFirewall || false,
            abortController: controller,
            onChunk: (chunk) => {
              if (typeof chunk === 'string') {
                accumulated += chunk;
                setGroupLiveSlot(prev => prev ? { ...prev, text: accumulated } : null);
              }
            },
            onDone: async () => {
              if (!accumulated.trim()) { resolve(); return; }
              const newMsg = { role: 'assistant', persona_key: pKey, persona_name: p.name, persona_avatar: p.avatar, content: accumulated, is_observer: false };
              thread = [...thread, newMsg];
              setGroupHistory(prev => [...prev, newMsg]);
              await api.saveGroupMessage(sessionId, { username: USERNAME, ...newMsg });
              setGroupLiveSlot(null);
              resolve();
            },
            onError: (err) => { console.error('Group stream error:', err); resolve(); }
          });
        });
        if (controller.signal.aborted) break;
      }
      if (controller.signal.aborted) break;
    }

    // Observer fires once at the end
    if (groupObserver && !controller.signal.aborted) {
      const obs = personas[groupObserver];
      if (obs) {
        const obsCtx = `[You are silently observing this group conversation between ${memberNames}. Offer one brief, reflective observation. Do not lead or dominate the conversation.]`;
        const obsHistory = thread.map(m => ({
          role: m.persona_key === groupObserver ? 'assistant' : 'user',
          content: m.role === 'user' ? m.content : `[${m.persona_name}]: ${m.content}`
        }));
        let obsAccum = '';
        setGroupLiveSlot({ personaKey: groupObserver, name: obs.name, avatar: obs.avatar, text: '', isObserver: true });
        await new Promise((resolve) => {
          api.streamChatComplete({
            personaKey: groupObserver, message: obsCtx, username: USERNAME,
            chatHistory: obsHistory, modelId: groupMemberModels[groupObserver] || advancedOptions.baseModelId || 'openrouter/auto',
            apiKeys, bypass_firewall: advancedOptions.bypassFirewall || false,
            abortController: controller,
            onChunk: (chunk) => { if (typeof chunk === 'string') { obsAccum += chunk; setGroupLiveSlot(prev => prev ? { ...prev, text: obsAccum } : null); } },
            onDone: async () => {
              if (obsAccum.trim()) {
                const obsMsg = { role: 'assistant', persona_key: groupObserver, persona_name: obs.name, persona_avatar: obs.avatar, content: obsAccum, is_observer: true };
                setGroupHistory(prev => [...prev, obsMsg]);
                await api.saveGroupMessage(sessionId, { username: USERNAME, ...obsMsg });
              }
              setGroupLiveSlot(null);
              resolve();
            },
            onError: () => { resolve(); }
          });
        });
      }
    }

    setGroupStreaming(false);
    setGroupLiveSlot(null);
    // Restore individual persona histories to undo backend auto-save contamination
    await restorePersonaHistories();
  };

  const startGroupSession = async () => {
    const sessionId = getGroupSessionId();
    const hist = await api.fetchGroupHistory(sessionId, USERNAME);
    setGroupHistory(hist.length > 0 ? hist : []);
    
    // Add to our tracked sessions list so it shows in the dropdown
    setAllGroupSessions(prev => {
      if (!prev.includes(sessionId)) return [...prev, sessionId];
      return prev;
    });

    // Snapshot each member's real individual chat history before group rounds contaminate it
    const snapshots = {};
    for (const key of groupMembers) {
      snapshots[key] = await api.fetchChatHistory(key, USERNAME, 100);
    }
    personaSnapshotsRef.current = snapshots;
    setGroupSessionStarted(true);
  };

  const restorePersonaHistories = async () => {
    for (const [pKey, msgs] of Object.entries(personaSnapshotsRef.current)) {
      const current = await api.fetchChatHistory(pKey, USERNAME, 200);
      if (current.length !== msgs.length) {
        await api.clearChatHistory(pKey, USERNAME);
        for (const msg of msgs) {
          await api.saveChatMessage(pKey, USERNAME, msg.role, msg.content);
        }
      }
    }
  };

  const GROUP_COLORS = ['#b060ff', '#00e5ff', '#ff6eb4', '#00cc88', '#ffaa00', '#ff6060'];
  const getPersonaColor = (pKey) => {
    const keys = groupMembers.filter(k => k !== groupObserver);
    const idx = keys.indexOf(pKey);
    return GROUP_COLORS[idx % GROUP_COLORS.length] || '#888';
  };

  const renderGroupChat = () => {
    const isSetup = !groupSessionStarted || groupMembers.length < 2;

    if (isSetup) {
      return (
        <div style={{ display: 'flex', flexDirection: 'column', height: '100%', padding: '30px', paddingLeft: !isSidebarOpen ? '70px' : '30px', overflowY: 'auto', transition: 'padding-left 0.2s' }}>
          <h2 style={{ color: 'var(--primary-color)', fontFamily: 'var(--font-marker)', marginBottom: '6px', fontSize: '22px' }}>GROUP CHAT</h2>
          <p style={{ color: 'var(--text-dim)', fontSize: '13px', marginBottom: '24px', fontFamily: 'var(--font-inter)' }}>Click personas to add them (2–4). Configure models and optionally set one as Observer below.</p>

          {/* ── PHASE 1: PERSONA PICKER ── */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(110px, 1fr))', gap: '10px', marginBottom: '28px', maxWidth: '600px' }}>
            {Object.entries(personas).map(([key, p]) => {
              const isSelected = groupMembers.includes(key);
              return (
                <div key={key} className={`group-persona-card ${isSelected ? 'selected' : ''}`}
                  onClick={() => setGroupMembers(prev => isSelected ? prev.filter(k => k !== key) : prev.length < 4 ? [...prev, key] : prev)}
                >
                  <div style={{ fontSize: '24px', marginBottom: '4px' }}>{p.avatar}</div>
                  <div style={{ fontSize: '12px', fontWeight: '600', color: isSelected ? 'var(--primary-color)' : 'var(--text-color)', fontFamily: 'var(--font-inter)' }}>{p.name}</div>
                </div>
              );
            })}
          </div>

          {/* ── PHASE 2: SESSION CONFIG ── */}
          {groupMembers.length > 0 && (
            <div style={{ marginBottom: '28px', maxWidth: '640px' }}>
              <div style={{ fontSize: '10px', fontWeight: '700', letterSpacing: '0.12em', textTransform: 'uppercase', color: 'var(--text-dim)', fontFamily: 'var(--font-inter)', marginBottom: '12px', opacity: 0.7 }}>
                Session Config
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '32px 130px 1fr 60px', gap: '0 16px', alignItems: 'center', marginBottom: '8px', paddingBottom: '6px', borderBottom: '1px solid rgba(255,255,255,0.07)' }}>
                <div />
                <div style={{ fontSize: '10px', color: 'var(--text-dim)', fontFamily: 'var(--font-inter)', textTransform: 'uppercase', letterSpacing: '0.1em' }}>Persona</div>
                <div style={{ fontSize: '10px', color: 'var(--text-dim)', fontFamily: 'var(--font-inter)', textTransform: 'uppercase', letterSpacing: '0.1em' }}>
                  Model ID <span style={{ opacity: 0.45, textTransform: 'none', letterSpacing: 0 }}>(blank = global default)</span>
                </div>
                <div style={{ fontSize: '10px', color: '#b060ff', fontFamily: 'var(--font-inter)', textTransform: 'uppercase', letterSpacing: '0.1em', textAlign: 'center' }}>Observer</div>
              </div>

              {groupMembers.map(key => {
                const p = personas[key];
                if (!p) return null;
                const isObs = groupObserver === key;
                return (
                  <div key={key} style={{ display: 'grid', gridTemplateColumns: '32px 130px 1fr 60px', gap: '0 16px', alignItems: 'center', padding: '9px 0', borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
                    <div style={{ fontSize: '20px' }}>{p.avatar}</div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                      <span style={{ fontSize: '13px', fontFamily: 'var(--font-inter)', color: isObs ? '#b060ff' : 'var(--text-color)', fontWeight: isObs ? '600' : '400' }}>
                        {p.name}
                      </span>
                      {isObs && <span style={{ fontSize: '9px', color: '#b060ff', opacity: 0.8 }}>👁</span>}
                      <button
                        onClick={() => { setGroupMembers(prev => prev.filter(k => k !== key)); if (isObs) setGroupObserver(null); }}
                        style={{ background: 'transparent', border: 'none', color: 'var(--text-dim)', cursor: 'pointer', fontSize: '11px', padding: '0 4px', opacity: 0.45, marginLeft: 'auto' }}
                        title="Remove from session"
                      >✕</button>
                    </div>
                    <input
                      type="text"
                      placeholder={advancedOptions.baseModelId || 'e.g. anthropic/claude-opus-4.5'}
                      value={groupMemberModels[key] || ''}
                      onChange={e => setGroupMemberModels(prev => ({ ...prev, [key]: e.target.value }))}
                      style={{
                        background: 'transparent', border: 'none',
                        borderBottom: '1px solid rgba(176,96,255,0.25)',
                        color: 'var(--text-color)', fontSize: '12px',
                        fontFamily: 'var(--font-inter)', outline: 'none',
                        padding: '2px 0', width: '100%'
                      }}
                    />
                    <div style={{ display: 'flex', justifyContent: 'center' }}>
                      <button
                        onClick={() => setGroupObserver(isObs ? null : key)}
                        title={isObs ? 'Remove observer role' : 'Set as observer (silent, reflects at end of round)'}
                        style={{
                          width: '18px', height: '18px', borderRadius: '50%',
                          background: isObs ? '#b060ff' : 'transparent',
                          border: `2px solid ${isObs ? '#b060ff' : 'rgba(176,96,255,0.4)'}`,
                          cursor: 'pointer', flexShrink: 0,
                          boxShadow: isObs ? '0 0 8px rgba(176,96,255,0.5)' : 'none',
                          transition: 'all 0.15s'
                        }}
                      />
                    </div>
                  </div>
                );
              })}
            </div>
          )}

          {groupMembers.length > 0 && (
            <div style={{ marginBottom: '24px', display: 'flex', alignItems: 'center', gap: '12px' }}>
              <label style={{ color: 'var(--text-dim)', fontSize: '13px', fontFamily: 'var(--font-inter)' }}>Session Name:</label>
              <input 
                list="session-namespaces" 
                value={groupSessionName}
                onChange={(e) => setGroupSessionName(e.target.value)}
                placeholder="main"
                style={{
                  background: 'rgba(0,0,0,0.2)', border: '1px solid rgba(255,255,255,0.1)',
                  color: 'var(--text-color)', fontSize: '13px', fontFamily: 'var(--font-inter)',
                  padding: '6px 10px', borderRadius: '4px', outline: 'none', flex: 1, maxWidth: '200px'
                }}
              />
              <datalist id="session-namespaces">
                {(() => {
                  const basePrefix = [...groupMembers].sort().join('_') + '_' + USERNAME + '_';
                  const namespaces = allGroupSessions
                    .filter(s => s.startsWith(basePrefix))
                    .map(s => s.replace(basePrefix, ''));
                  if (!namespaces.includes('main')) namespaces.unshift('main');
                  return [...new Set(namespaces)].map(ns => <option key={ns} value={ns} />);
                })()}
              </datalist>
              <button
                title="Delete this session"
                onClick={async () => {
                  if (window.confirm(`Delete the entire "${groupSessionName}" session for this group?`)) {
                    const sid = getGroupSessionId();
                    await api.clearGroupHistory(sid, USERNAME);
                    setAllGroupSessions(prev => prev.filter(s => s !== sid));
                    setGroupSessionName("main");
                  }
                }}
                style={{
                  background: 'transparent', border: 'none', color: '#ed4245', cursor: 'pointer',
                  opacity: 0.7, padding: '4px', display: 'flex', alignItems: 'center'
                }}
                onMouseEnter={e => e.currentTarget.style.opacity = '1'}
                onMouseLeave={e => e.currentTarget.style.opacity = '0.7'}
              >
                <span className="material-icons" style={{ fontSize: '18px' }}>delete</span>
              </button>
            </div>
          )}

          <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '24px', maxWidth: '280px' }}>
            <label style={{ color: 'var(--text-dim)', fontSize: '13px', fontFamily: 'var(--font-inter)' }}>Turns per persona:</label>
            <input type="range" min={1} max={4} value={groupTurnLimit} onChange={e => setGroupTurnLimit(Number(e.target.value))}
              style={{ flex: 1, accentColor: 'var(--primary-color)' }} />
            <span style={{ color: 'var(--primary-color)', fontSize: '16px', fontWeight: '700', minWidth: '20px' }}>{groupTurnLimit}</span>
          </div>

          <button
            disabled={groupMembers.length < 2}
            onClick={() => startGroupSession()}
            style={{
              padding: '12px 0',
              background: 'transparent',
              border: 'none',
              color: groupMembers.length >= 2 ? 'var(--primary-color)' : 'var(--text-dim)',
              cursor: groupMembers.length >= 2 ? 'pointer' : 'not-allowed',
              fontFamily: 'var(--font-marker)',
              fontSize: '16px',
              letterSpacing: '2px',
              opacity: groupMembers.length >= 2 ? 1 : 0.4
            }}
          >
            START SESSION ({groupMembers.length} selected)
          </button>
        </div>
      );
    }

    // CHAT VIEW
    return (
      <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
        <div style={{ padding: '12px 20px', paddingLeft: !isSidebarOpen ? '70px' : '20px', borderBottom: 'var(--border-dashed)', display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexShrink: 0, transition: 'padding-left 0.2s' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            {groupMembers.map(k => (
              <span key={k} style={{ fontSize: '20px' }} title={personas[k]?.name}>{personas[k]?.avatar}</span>
            ))}
            <span style={{ fontSize: '12px', color: 'var(--text-dim)', fontFamily: 'var(--font-inter)', marginLeft: '4px' }}>{groupTurnLimit} turn{groupTurnLimit !== 1 ? 's' : ''}</span>
          </div>
          <div style={{ display: 'flex', gap: '10px' }}>
            {groupStreaming && (
              <button onClick={() => { groupAbortRef.current?.abort(); setGroupStreaming(false); setGroupLiveSlot(null); }}
                style={{ padding: '4px 12px', background: 'transparent', border: '1px solid #ff4444', color: '#ff4444', borderRadius: '4px', cursor: 'pointer', fontSize: '12px', fontFamily: 'var(--font-inter)' }}>
                ✕ Interrupt
              </button>
            )}
            <button onClick={async () => {
              if (window.confirm('Wipe history for this group? This cannot be undone.')) {
                const sessionId = getGroupSessionId();
                await api.clearGroupHistory(sessionId, USERNAME);
                setGroupHistory([]);
              }
            }}
              style={{ padding: '4px 12px', background: 'transparent', border: 'none', color: '#b060ff', cursor: 'pointer', fontSize: '12px', fontFamily: 'var(--font-inter)' }}>
              Wipe History
            </button>
            <button onClick={async () => { 
              await restorePersonaHistories();
              setGroupMembers([]); 
              setGroupObserver(null); 
              setGroupHistory([]); 
              setGroupStreaming(false); 
              setGroupLiveSlot(null); 
              setGroupSessionStarted(false); 
              setGroupMemberModels({}); 
            }}
              style={{ padding: '4px 12px', background: 'transparent', border: 'none', color: 'var(--text-dim)', cursor: 'pointer', fontSize: '12px', fontFamily: 'var(--font-inter)' }}>
              End Session
            </button>
          </div>
        </div>

        <div style={{ flex: 1, overflowY: 'auto', padding: '20px', display: 'flex', flexDirection: 'column', gap: '14px' }}>
          {groupHistory.map((msg, i) => {
            const isUser = msg.role === 'user';
            const color = isUser ? 'var(--text-dim)' : msg.is_observer ? '#b060ff' : getPersonaColor(msg.persona_key);
            return (
              <div key={msg.id || i} className={`group-message ${msg.is_observer ? 'observer' : ''}`} style={{ borderLeftColor: color }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '4px' }}>
                  <span style={{ fontSize: '16px' }}>{msg.persona_avatar}</span>
                  <span style={{ fontSize: '11px', fontWeight: '700', color, fontFamily: 'var(--font-inter)', textTransform: 'uppercase', letterSpacing: '0.08em' }}>
                    {msg.persona_name}{msg.is_observer ? ' · Observer' : ''}
                  </span>
                  {msg.id && (
                    <button
                      className="material-icons delete-msg-btn"
                      onClick={async () => {
                        if (window.confirm("Delete this group message?")) {
                          const success = await api.deleteGroupChatMessage(msg.id);
                          if (success) {
                            setGroupHistory(prev => prev.filter(m => m.id !== msg.id));
                          }
                        }
                      }}
                      style={{
                        marginLeft: 'auto',
                        background: 'transparent', border: 'none', color: 'var(--text-dim)',
                        cursor: 'pointer', fontSize: '14px', opacity: 0.5,
                        transition: 'opacity 0.2s', padding: '4px'
                      }}
                      onMouseEnter={e => e.currentTarget.style.opacity = 1}
                      onMouseLeave={e => e.currentTarget.style.opacity = 0.5}
                      title="Delete message"
                    >
                      delete
                    </button>
                  )}
                </div>
                <div
                  className="group-text-content"
                  style={{ color: 'var(--text-color)', fontFamily: 'var(--font-inter)', lineHeight: 1.55, fontStyle: msg.is_observer ? 'italic' : 'normal', opacity: msg.is_observer ? 0.8 : 1 }}
                  dangerouslySetInnerHTML={{ __html: formatMessage(msg.content) }}
                />
              </div>
            );
          })}

          {groupLiveSlot && (
            <div className={`group-message ${groupLiveSlot.isObserver ? 'observer' : ''} group-live`}
              style={{ borderLeftColor: groupLiveSlot.isObserver ? '#b060ff' : getPersonaColor(groupLiveSlot.personaKey) }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '4px' }}>
                <span style={{ fontSize: '16px' }}>{groupLiveSlot.avatar}</span>
                <span style={{ fontSize: '11px', fontWeight: '700', color: groupLiveSlot.isObserver ? '#b060ff' : getPersonaColor(groupLiveSlot.personaKey), fontFamily: 'var(--font-inter)', textTransform: 'uppercase', letterSpacing: '0.08em' }}>
                  {groupLiveSlot.name}{groupLiveSlot.isObserver ? ' · Observer' : ''}
                </span>
                <span className="group-live-indicator">speaking</span>
              </div>
              <div
                className="group-text-content"
                style={{ color: 'var(--text-color)', fontFamily: 'var(--font-inter)', lineHeight: 1.55, fontStyle: groupLiveSlot.isObserver ? 'italic' : 'normal' }}
                dangerouslySetInnerHTML={{ __html: formatMessage(groupLiveSlot.text) }}
              />
              <span className="streaming-cursor">▋</span>
            </div>
          )}

          <div ref={groupEndRef} />
        </div>

        <div style={{ padding: '16px 20px', borderTop: 'var(--border-dashed)', flexShrink: 0 }}>
          <div style={{ position: 'relative', maxWidth: '100%' }}>
            <textarea
              id="group-chat-input"
              disabled={groupStreaming}
              placeholder={groupStreaming ? 'Conversation in progress...' : 'Message the group...'}
              rows={3}
              style={{ width: '100%', background: 'rgba(10,10,10,0.8)', border: '1px dashed var(--accent-purple)', borderRadius: '8px', padding: '12px 50px 12px 16px', color: 'var(--text-color)', fontFamily: 'var(--font-inter)', fontSize: '14px', resize: 'none', outline: 'none', opacity: groupStreaming ? 0.5 : 1, boxSizing: 'border-box' }}
            />
            <button
              className="send-button material-icons"
              disabled={groupStreaming}
              onClick={() => {
                const el = document.getElementById('group-chat-input');
                if (!el) return;
                const val = el.value.trim();
                if (!val || groupStreaming) return;
                el.value = '';
                runGroupRound(val);
              }}
              style={{ position: 'absolute', bottom: '12px', right: '12px', background: 'transparent', border: 'none', color: 'var(--primary-color)', cursor: 'pointer', fontSize: '24px' }}
            >
              send
            </button>
          </div>
        </div>
      </div>
    );
  };

  // --- PANEL RESIZE LOGIC ---
  const startResize = (e, panel) => {
    e.preventDefault();
    const startX = e.clientX;
    const startVal = panel === 'sidebar' ? sidebarWidth
      : panel === 'fileTree' ? fileTreeWidth
      : chatPanelWidth;

    resizingRef.current = panel;

    const onMove = (ev) => {
      const delta = ev.clientX - startX;
      if (panel === 'sidebar') {
        const next = Math.min(520, Math.max(160, startVal + delta));
        setSidebarWidth(next);
        localStorage.setItem('q_sidebar_w', next);
      } else if (panel === 'fileTree') {
        const next = Math.min(480, Math.max(140, startVal + delta));
        setFileTreeWidth(next);
        localStorage.setItem('q_filetree_w', next);
      } else {
        const next = Math.min(640, Math.max(240, startVal - delta));
        setChatPanelWidth(next);
        localStorage.setItem('q_chat_w', next);
      }
    };

    const onUp = () => {
      resizingRef.current = null;
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };

    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  };


  const [USERNAME, setUSERNAME] = useState(() => {
    let saved = localStorage.getItem('persona_username');
    if (!saved) {
      saved = window.prompt("Welcome to Q. What is your name?", "Sky");
      if (!saved || !saved.trim()) saved = "Sky";
      localStorage.setItem('persona_username', saved.trim());
    }
    return saved.trim();
  });

  // Fetch actual personas from FastAPI DB on load
  useEffect(() => {
    const initData = async () => {
      const data = await api.fetchPersonas(USERNAME);
      if (data) {
        setPersonas(data);
        // Default to rick if available
        if (data["rick"]) {
          setActivePersona("rick");
        }
      }
      const sessions = await api.fetchUserGroupSessions(USERNAME);
      setAllGroupSessions(sessions);
    };
    initData();
  }, []);

  // When persona changes in workshop, load its lore entries
  useEffect(() => {
    if (!newPersona.originalKey) {
      setLoreEntries([]);
      return;
    }
    const loadLore = async () => {
      setLoreLoading(true);
      const entries = await api.fetchLoreEntries(newPersona.originalKey, USERNAME);
      setLoreEntries(entries);
      setLoreLoading(false);
    };
    loadLore();
    setLoreForm({ title: '', content: '' });
    setLoreEditId(null);
  }, [newPersona.originalKey]);

  // Polling for Lore Sync (Zettel Hyperscaling visibility)
  useEffect(() => {
    const anyUnprocessed = loreEntries.some(e => !e.processed);
    if (anyUnprocessed && currentView === 'studio' && newPersona.originalKey) {
      const t = setTimeout(async () => {
        const updated = await api.fetchLoreEntries(newPersona.originalKey, USERNAME);
        setLoreEntries(updated);
      }, 5000);
      return () => clearTimeout(t);
    }
  }, [loreEntries, currentView, newPersona.originalKey]);

  // When persona changes, load their chat history
  useEffect(() => {
    if (!activePersona) return;

    // Clear the active buffer
    setLiveStreamText("");

    const loadHistory = async () => {
      const history = await api.fetchChatHistory(activePersona, USERNAME, 50);
      setChatHistory(history);
    };

    loadHistory();
  }, [activePersona, personas]);

  // Auto-scroll to bottom of chat
  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [chatHistory, liveStreamText]);

  const handleSend = async () => {
    if (!currentInput.trim() && chatAttachments.length === 0) return;
    if (isStreaming) return;

    let userMessage;
    if (chatAttachments.length === 0) {
      userMessage = currentInput;
    } else {
      userMessage = [];
      if (currentInput.trim()) {
        userMessage.push({ type: "text", text: currentInput });
      }
      userMessage = [...userMessage, ...chatAttachments];
    }

    const displayMessage = currentInput || "[Multimodal Attachment]";
    setCurrentInput(""); // Clear box instantly
    setChatAttachments([]);

    // Update local UI immediately
    const newHistory = [...chatHistory, { role: "user", content: displayMessage }];
    setChatHistory(newHistory);
    setIsStreaming(true);
    setLiveStreamText("");

    // Start the stream
    abortControllerRef.current = new AbortController();
    await api.streamChatComplete({
      personaKey: activePersona,
      message: userMessage,
      username: USERNAME,
      chatHistory: newHistory.slice(0, -1),
      abortController: abortControllerRef.current,
      modelId: advancedOptions.baseModelId,
      expertModelId: advancedOptions.expertModelId,
      temperature: advancedOptions.temperature,
      topP: advancedOptions.topP,
      topK: advancedOptions.topK,
      presencePenalty: advancedOptions.presencePenalty,
      frequencyPenalty: advancedOptions.frequencyPenalty,
      thinkingLevel: advancedOptions.thinkingLevel,
      customBaseUrl: advancedOptions.customBaseUrl,
      customProviderType: advancedOptions.customProviderType,
      customAuthHeaderName: advancedOptions.customAuthHeaderName,
      customAuthPrefix: advancedOptions.customAuthPrefix,
      bypass_firewall: advancedOptions.bypassFirewall,
      apiKeys: {
        universal: apiKeys.universal
      },
      workspaceContext: globalMode === 'workspace' ? {
        activeFile: activeFilePath,
        currentCode: workspaceCode
      } : null,
      onChunk: (chunk) => {
        if (typeof chunk === 'object' && chunk.type === 'control') {
          if (chunk.event === 'reflection_started') {
            setIsReflecting(true);
          }
        } else {
          setLiveStreamText((prev) => prev + chunk);
        }
      },
      onDone: async (fullContent) => {
        if (fullContent) {
          await api.saveChatMessage(activePersona, USERNAME, "assistant", fullContent);
        }
        setIsStreaming(false);
        // We leave isReflecting alone here; it finishes asynchronously in the backend thread
        // For simplicity, we'll turn it off after a few seconds or when the next message is sent
        setTimeout(() => setIsReflecting(false), 5000); // Hacky fallback to clear it eventually
        const updatedHistory = await api.fetchChatHistory(activePersona, USERNAME, 50);
        setChatHistory(updatedHistory);
        setLiveStreamText("");

        // Artifact Interception: If code blocks found, teleport them to Workspace
        const artifact = extractArtifacts(fullContent);
        if (artifact) {
          setWorkspaceCode(artifact.code);
          if (artifact.lang) setEditorLanguage(artifact.lang);
        }
      },
      onError: (err) => {
        console.error(err);
        setLiveStreamText("⚠️ Connection Error: " + err);
        setIsStreaming(false);
      }
    });
  };

  // High-Fidelity Formatter for Rick's formatting (bold, etc)
  const formatMessage = (text) => {
    if (!text) return "";
    let formatted = text
      .replace(/\*\*(.*?)\*\*/g, '<b>$1</b>') // Bold
      .replace(/\*(.*?)\*/g, '<i>$1</i>')     // Italic
      .replace(/`(.*?)`/g, '<code>$1</code>'); // Inline Code
    return formatted;
  };

  const extractArtifacts = (text) => {
    if (!text) return null;
    // Capture group 1: Language Tag, Group 2: Content
    const regex = /```([a-zA-Z]*)\n([\s\S]*?)```/g;
    const matches = [...text.matchAll(regex)];
    if (matches.length > 0) {
      const lastMatch = matches[matches.length - 1];
      return {
        lang: lastMatch[1] || 'python',
        code: lastMatch[2]
      };
    }
    return null;
  };

  const loadWorkspaceTree = async () => {
    try {
      const tree = await api.fetchFileTree(workspacePath);
      setFileTree(tree);
      // Sync to the real absolute path the backend resolved — fixes the './C:\...' malformed path bug
      if (tree && tree.path) {
        setWorkspacePath(tree.path);
      }
    } catch (err) {
      console.error("Failed to load workspace tree:", err);
    }
  };

  const toggleFolder = (path) => {
    setExpandedPaths(prev => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  };

  const handleCreateItem = async (type) => {
    const name = prompt(`New ${type} name (e.g. main.py)\nTo switch workspace, paste a path into the path bar above instead.`);
    if (!name) return;

    // Safety valve: if user pastes an absolute path here, remount instead of erroring
    const isAbsPath = /^[A-Za-z]:[\\\/]/.test(name) || name.startsWith('/');
    if (isAbsPath) {
      setWorkspacePath(name);
      await loadWorkspaceTree();
      return;
    }

    try {
      const sep = workspacePath.includes('\\') ? '\\' : '/';
      const base = workspacePath.replace(/[\/\\]+$/, '');
      const path = `${base}${sep}${name}`;
      await api.createItem(path, type);
      loadWorkspaceTree();
    } catch (err) {
      alert("Failed to create item: " + err.message);
    }
  };

  const handleDeleteItem = async (e, path) => {
    e.stopPropagation();
    if (!confirm(`Permanently delete ${path}?`)) return;
    try {
      await api.deleteItem(path);
      if (activeFilePath === path) {
        setActiveFilePath("");
        setWorkspaceCode("// File deleted.");
      }
      loadWorkspaceTree();
    } catch (err) {
      alert("Failed to delete: " + err.message);
    }
  };

  const handleSaveFile = async () => {
    if (!activeFilePath) return;
    try {
      const res = await api.saveFileContent(activeFilePath, workspaceCode);
      if (res.success) alert("Saved successfully.");
      else alert("Save failed: " + res.error);
    } catch (err) {
      alert("Save error: " + err.message);
    }
  };

  const handleFileDrop = async (e) => {
    e.preventDefault();
    setIsDraggingOver(false);
    const files = e.dataTransfer.files;
    if (!files || files.length === 0) return;

    const sep = workspacePath.includes('\\') ? '\\' : '/';
    const base = workspacePath.replace(/[\/\\]+$/, '');
    let lastPath = null;
    let lastContent = null;

    const readAndSave = (file) => new Promise((resolve) => {
      const reader = new FileReader();
      reader.onload = async (event) => {
        const content = event.target.result;
        const path = `${base}${sep}${file.name}`;
        await api.saveFileContent(path, content);
        lastPath = path;
        lastContent = content;
        resolve();
      };
      reader.onerror = () => resolve(); // skip unreadable files silently
      reader.readAsText(file);
    });

    for (const file of Array.from(files)) {
      await readAndSave(file);
    }

    loadWorkspaceTree();

    // Auto-open the last dropped file in the editor
    if (lastPath && lastContent !== null) {
      setActiveFilePath(lastPath);
      setWorkspaceCode(lastContent);
      const ext = lastPath.split('.').pop();
      const langMap = { py: 'python', js: 'javascript', jsx: 'javascript', ts: 'typescript', tsx: 'typescript', css: 'css', html: 'html', json: 'json', md: 'markdown', txt: 'text' };
      setEditorLanguage(langMap[ext] || 'text');
    }
  };

  // Native OS file picker — open any file from disk, display in editor without saving
  const handleOpenFilePicker = () => {
    const input = document.createElement('input');
    input.type = 'file';
    input.multiple = false;
    input.onchange = (e) => {
      const file = e.target.files[0];
      if (!file) return;
      const reader = new FileReader();
      reader.onload = (ev) => {
        const content = ev.target.result;
        setWorkspaceCode(content);
        setActiveFilePath(file.name); // display name only, not saved to disk
        const ext = file.name.split('.').pop();
        const langMap = { py: 'python', js: 'javascript', jsx: 'javascript', ts: 'typescript', tsx: 'typescript', css: 'css', html: 'html', json: 'json', md: 'markdown', txt: 'text' };
        setEditorLanguage(langMap[ext] || 'text');
      };
      reader.readAsText(file);
    };
    input.click();
  };


  const handleFileClick = async (file) => {
    if (file.type === 'directory') return;
    try {
      setActiveFilePath(file.path);
      const content = await api.fetchFileContent(file.path);
      setWorkspaceCode(content);
      // Auto-detect language from extension
      const ext = file.name.split('.').pop();
      const langMap = { 'py': 'python', 'js': 'javascript', 'jsx': 'javascript', 'css': 'css', 'html': 'html', 'json': 'json', 'md': 'markdown' };
      setEditorLanguage(langMap[ext] || 'text');
    } catch (err) {
      console.error("Failed to load file content:", err);
    }
  };

  useEffect(() => {
    if (globalMode === 'workspace') {
      loadWorkspaceTree();
    }
  }, [globalMode]);

  const renderFileTree = (node) => {
    if (!node) return null;
    const isExpanded = expandedPaths.has(node.path);

    return (
      <div key={node.path} style={{ marginLeft: node.type === 'directory' ? '12px' : '20px', fontSize: '13px' }}>
        <div
          onClick={() => node.type === 'directory' ? toggleFolder(node.path) : handleFileClick(node)}
          style={{
            cursor: 'pointer',
            color: activeFilePath === node.path ? 'var(--work-green)' : 'var(--text-color)',
            padding: '4px 0',
            whiteSpace: 'nowrap',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            display: 'flex',
            alignItems: 'center',
            gap: '6px'
          }}
          className="file-tree-item"
        >
          {node.type === 'directory' && (
            <span className="material-icons" style={{ fontSize: '14px', transition: 'transform 0.15s ease', transform: isExpanded ? 'rotate(90deg)' : 'none' }}>
              chevron_right
            </span>
          )}
          <span className="material-icons" style={{ fontSize: '16px' }}>
            {node.type === 'directory' ? 'folder' : 'description'}
          </span>
          <span style={{ fontFamily: 'var(--font-inter)', opacity: 0.9 }}>{node.name}</span>
          <span
            onClick={(e) => handleDeleteItem(e, node.path)}
            style={{ color: 'var(--primary-color)', marginLeft: '10px', opacity: 0.8, fontSize: '10px', fontWeight: 'bold', cursor: 'pointer' }}
            title="Purge Signal"
          >
            x
          </span>
        </div>
        {node.type === 'directory' && isExpanded && node.children && (
          <div className="directory-children shadow-inner">
            {node.children.map(child => renderFileTree(child))}
          </div>
        )}
      </div>
    );
  };



  const renderChatInterface = () => (
    <>
      <div className="chat-header animate-fade-in">
        <div className="chat-header-info" style={{ display: 'flex', alignItems: 'center', gap: '15px' }}>
          <h2>{personas[activePersona].name}</h2>
          {isReflecting && (
            <div style={{
              display: 'flex', alignItems: 'center', gap: '6px',
              fontSize: '12px', color: '#b060ff',
              background: 'rgba(176, 96, 255, 0.1)',
              padding: '4px 8px', borderRadius: '12px',
              border: '1px solid rgba(176, 96, 255, 0.3)'
            }}>
              <span className="material-icons glow-pulse" style={{ fontSize: '14px' }}>psychology</span>
              Synthesizing Observations
            </div>
          )}
        </div>
      </div>

      <div className="chat-history">
        {chatHistory.map((msg, idx) => (
          <div key={idx} className={`message-box ${msg.role === 'user' ? 'user' : 'assistant'}`} style={{ position: 'relative' }}>
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '8px' }}>
              <div className="message-avatar">
                {msg.role === 'user' ? userAvatar : (personas[activePersona]?.avatar || '🤖')}
              </div>
              {msg.id && (
                <button
                  className="material-icons delete-msg-btn"
                  onClick={async () => {
                    if (window.confirm("Delete this message?")) {
                      const success = await api.deleteChatMessage(msg.id);
                      if (success) {
                        setChatHistory(prev => prev.filter(m => m.id !== msg.id));
                      }
                    }
                  }}
                  style={{
                    background: 'transparent', border: 'none', color: 'var(--text-dim)',
                    cursor: 'pointer', fontSize: '14px', opacity: 0.5,
                    transition: 'opacity 0.2s', padding: '4px', marginTop: '4px'
                  }}
                  onMouseEnter={e => e.currentTarget.style.opacity = 1}
                  onMouseLeave={e => e.currentTarget.style.opacity = 0.5}
                  title="Delete message"
                >
                  delete
                </button>
              )}
            </div>
            <div
              className="message-content"
              dangerouslySetInnerHTML={{ __html: formatMessage(msg.content) }}
            />
          </div>
        ))}

        {/* Streaming actively... */}
        {(isStreaming || liveStreamText) && (
          <div className="message-box assistant">
            <div className="message-avatar glow-pulse">
              {personas[activePersona]?.avatar || '🤖'}
            </div>
            <div
              className="message-content"
              style={{ border: '1px solid var(--accent-void)' }}
              dangerouslySetInnerHTML={{ __html: formatMessage(liveStreamText) }}
            />
            {isStreaming && <span className="streaming-cursor">█</span>}
          </div>
        )}

        <div ref={chatEndRef} />
      </div>

      <div className="chat-input-container">
        {chatAttachments.length > 0 && (
          <div style={{ display: 'flex', gap: '10px', padding: '10px', overflowX: 'auto', background: 'rgba(0,0,0,0.5)', borderTopLeftRadius: '8px', borderTopRightRadius: '8px' }}>
            {chatAttachments.map((att, idx) => (
              <div key={idx} style={{ position: 'relative', width: '60px', height: '60px', borderRadius: '4px', overflow: 'hidden', border: '1px solid var(--primary-color)' }}>
                {att.type === 'image_url' ? (
                  <img src={att.image_url.url} alt="attachment" style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
                ) : (
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', background: '#333' }}><span className="material-icons">description</span></div>
                )}
                <button onClick={() => setChatAttachments(prev => prev.filter((_, i) => i !== idx))} style={{ position: 'absolute', top: 0, right: 0, background: 'rgba(255,0,0,0.7)', border: 'none', color: 'white', cursor: 'pointer', fontSize: '10px', padding: '4px' }}>✕</button>
              </div>
            ))}
          </div>
        )}
        <div className="chat-input-wrapper">
          <input type="file" id="chat_file_upload" style={{ display: 'none' }} multiple accept="image/*,text/*" onChange={async (e) => {
            const files = Array.from(e.target.files);
            if (!files.length) return;
            for (const file of files) {
              if (file.type.startsWith('image/')) {
                const reader = new FileReader();
                reader.onload = (ev) => {
                  setChatAttachments(prev => [...prev, { type: "image_url", image_url: { url: ev.target.result } }]);
                };
                reader.readAsDataURL(file);
              } else {
                const reader = new FileReader();
                reader.onload = (ev) => {
                  setChatAttachments(prev => [...prev, { type: "text", text: `\n[FILE: ${file.name}]\n${ev.target.result}\n[/FILE]` }]);
                };
                reader.readAsText(file);
              }
            }
            e.target.value = null;
          }} />
          <button
            className="material-icons attach-button"
            title="Attach File"
            onClick={() => document.getElementById('chat_file_upload').click()}
          >
            add
          </button>
          <textarea
            className="chat-input glass-panel"
            placeholder={`Send a message to ${personas[activePersona]?.name}...`}
            rows={1}
            value={currentInput}
            onChange={(e) => setCurrentInput(e.target.value)}
            disabled={isStreaming}
          />

          {isStreaming ? (
            <button
              className="send-button material-icons"
              style={{ color: '#ff4444' }}
              onClick={() => {
                if (abortControllerRef.current) {
                  abortControllerRef.current.abort();
                }
              }}
              title="Stop Generation"
            >
              stop
            </button>
          ) : (
            <button
              className="send-button material-icons"
              onClick={handleSend}
              disabled={!currentInput.trim()}
            >
              send
            </button>
          )}
        </div>
      </div>
    </>
  );

  const handleEditorWillMount = (monaco) => {
    monaco.editor.defineTheme('trenches-void', {
      base: 'vs-dark',
      inherit: false,
      rules: [
        { token: '', foreground: '00cc66' },
        { token: 'comment', foreground: '555555', fontStyle: 'italic' },
        { token: 'keyword', foreground: 'ff007f', fontStyle: 'bold' },
        { token: 'string', foreground: '00ffff' },
        { token: 'string.json', foreground: '00ffff' },
        { token: 'string.key.json', foreground: '00cc66' },
        { token: 'string.value.json', foreground: '00ffff' },
        { token: 'number', foreground: 'bf00ff' },
        { token: 'number.json', foreground: 'bf00ff' },
        { token: 'boolean', foreground: 'ff007f', fontStyle: 'bold' },
        { token: 'regexp', foreground: 'ff007f' },
        { token: 'type', foreground: '00cc66' },
        { token: 'class', foreground: '00ffff' },
        { token: 'function', foreground: '00ffbb' },
        { token: 'operator', foreground: '00cc66' },
        { token: 'delimiter', foreground: '00cc66' },
        { token: 'constant', foreground: 'bf00ff' },
        { token: 'variable', foreground: '00cc66' },
      ],
      colors: {
        'editor.background': '#000000',
        'editor.lineHighlightBackground': '#0a0a0a',
        'editor.selectionBackground': '#222222',
        'editorLineNumber.foreground': '#006633',
        'editorLineNumber.activeForeground': '#ff007f',
        'editor.foreground': '#00cc66',
        'editorCursor.foreground': '#ff007f',
        'editor.inactiveSelectionBackground': '#111111'
      }
    });
  };

  const getMonacoTheme = () => {
    if (appTheme === 'q-light') return 'vs';
    if (appTheme === 'q-dark') return 'vs-dark';
    return 'trenches-void';
  };

  return (
    <div className={`app-container ${!isSidebarOpen ? 'sidebar-collapsed' : ''}`}>
      <button
        className="sidebar-toggle-btn"
        onClick={() => setIsSidebarOpen(!isSidebarOpen)}
        title="Toggle Sidebar"
      >
        <span className="material-icons">menu</span>
      </button>

      {/* SIDEBAR */}
      <div
        className={`sidebar animate-fade-in glass-panel ${!isSidebarOpen ? 'collapsed' : ''}`}
        style={isSidebarOpen ? { width: sidebarWidth, flexShrink: 0 } : undefined}
      >
        <div className="sidebar-header">
          <div style={{ fontSize: '28px', color: '#b060ff' }} className="material-icons">local_fire_department</div>
        </div>



        {/* VIEW TOGGLE */}
        <div style={{ padding: '0 20px', marginBottom: '20px', paddingBottom: '10px', display: 'flex', flexDirection: 'column', gap: '15px' }}>
          <button
            style={{
              width: '100%',
              background: 'transparent',
              border: 'none',
              color: currentView === 'studio' && globalMode !== 'workspace' ? 'var(--primary-color)' : 'var(--text-color)',
              cursor: 'pointer',
              fontSize: '18px',
              textAlign: 'left',
              display: 'flex',
              alignItems: 'center',
              gap: '12px',
              transition: 'var(--transition-fast)'
            }}
            onMouseEnter={(e) => e.currentTarget.style.color = 'var(--primary-color)'}
            onMouseLeave={(e) => e.currentTarget.style.color = currentView === 'studio' && globalMode !== 'workspace' ? 'var(--primary-color)' : 'var(--text-color)'}
            onClick={() => { setCurrentView(currentView === 'chat' ? 'studio' : 'chat'); setGlobalMode('roleplay'); }}
          >
            <span style={{ fontSize: '22px' }}>{currentView === 'chat' ? '🔨' : '💬'}</span>
            <span style={{ fontFamily: 'var(--font-marker)' }}>{currentView === 'chat' ? 'THE WORKSHOP' : 'RETURN TO CHAT'}</span>
          </button>

          <button
            style={{
              width: '100%',
              background: 'transparent',
              border: 'none',
              color: globalMode === 'workspace' ? 'var(--primary-color)' : 'var(--text-color)',
              cursor: 'pointer',
              fontSize: '18px',
              textAlign: 'left',
              display: 'flex',
              alignItems: 'center',
              gap: '12px',
              transition: 'var(--transition-fast)'
            }}
            onMouseEnter={(e) => e.currentTarget.style.color = 'var(--primary-color)'}
            onMouseLeave={(e) => e.currentTarget.style.color = globalMode === 'workspace' ? 'var(--primary-color)' : 'var(--text-color)'}
            onClick={() => setGlobalMode(globalMode === 'workspace' ? 'roleplay' : 'workspace')}
          >
            <span style={{ fontSize: '22px' }}>💻</span>
            <span style={{ fontFamily: 'var(--font-marker)' }}>{globalMode === 'workspace' ? 'EXIT WORKSPACE' : 'WORKSPACE UI'}</span>
          </button>

          <button
            style={{
              width: '100%', background: 'transparent', border: 'none',
              color: currentView === 'group' ? 'var(--primary-color)' : 'var(--text-color)',
              cursor: 'pointer', fontSize: '14px', textAlign: 'left',
              display: 'flex', alignItems: 'center', gap: '12px',
              transition: 'var(--transition-fast)'
            }}
            onMouseEnter={(e) => e.currentTarget.style.color = 'var(--primary-color)'}
            onMouseLeave={(e) => e.currentTarget.style.color = currentView === 'group' ? 'var(--primary-color)' : 'var(--text-color)'}
            onClick={() => { setCurrentView('group'); setGlobalMode('roleplay'); }}
          >
            <span style={{ fontSize: '22px' }}>💬</span>
            <span style={{ fontFamily: 'var(--font-marker)' }}>GROUP CHAT</span>
          </button>

          <button
            style={{
              width: '100%', background: 'transparent', border: 'none',
              color: 'var(--text-color)',
              cursor: 'pointer', fontSize: '14px', textAlign: 'left',
              display: 'flex', alignItems: 'center', gap: '12px',
              transition: 'var(--transition-fast)'
            }}
            onMouseEnter={(e) => e.currentTarget.style.color = 'var(--primary-color)'}
            onMouseLeave={(e) => e.currentTarget.style.color = 'var(--text-color)'}
            onClick={() => setShowSettings(true)}
          >
            <span style={{ fontSize: '22px' }}>⚙️</span>
            <span style={{ fontFamily: 'var(--font-marker)' }}>SETTINGS</span>
          </button>
        </div>

        <div className="persona-list">
          {Object.entries(personas).map(([key, p]) => (
            <div
              key={key}
              className={`persona-item ${activePersona === key ? 'active' : ''}`}
              onClick={() => {
                if (currentView === 'group') return; // don't steal clicks in group chat
                setActivePersona(key);
                setCurrentView("chat");
                // Also update the workshop form so it stays synced
                setNewPersona({
                  originalKey: key,
                  key: key,
                  name: p.name,
                  avatar: p.avatar,
                  tagline: p.tagline,
                  system_prompt: p.system_prompt || '',
                  on_demand_files: p.on_demand_files || [],
                  access_code: p.access_code || '',
                  om_enabled: p.om_enabled !== false,
                  om_turn_threshold: p.om_turn_threshold !== undefined ? p.om_turn_threshold : 5,
                  deep_memory_enabled: !!p.deep_memory_enabled
                });
              }}
            >
              <div className="persona-avatar">{p.avatar}</div>
              <div className="persona-info">
                <span className="persona-name">{p.name}</span>
              </div>
            </div>
          ))}
        </div>
      </div>

        {/* SIDEBAR RESIZE HANDLE */}
        {isSidebarOpen && (
          <div
            onMouseDown={(e) => startResize(e, 'sidebar')}
            style={{
              width: '4px', flexShrink: 0, cursor: 'col-resize',
              background: 'transparent', transition: 'background 0.15s',
              zIndex: 20
            }}
            onMouseEnter={e => e.currentTarget.style.background = 'var(--primary-color)'}
            onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
            title="Drag to resize sidebar"
          />
        )}

      {/* MAIN CONTENT AREA */}
      {globalMode === 'workspace' ? (
        <div className="workspace-area animate-fade-in" style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
          <div
            className="workspace-pane"
            style={{
              width: fileTreeWidth, flexShrink: 0,
              borderRight: 'none', borderLeft: 'none', padding: '15px',
              transition: 'box-shadow 0.2s ease, background 0.2s ease',
              boxShadow: isDraggingOver ? '0 0 0 2px var(--work-green) inset' : 'none',
              background: isDraggingOver ? 'rgba(0,204,102,0.04)' : 'transparent',
              overflow: 'hidden'
            }}
            onDragOver={(e) => { e.preventDefault(); setIsDraggingOver(true); }}
            onDragLeave={() => setIsDraggingOver(false)}
            onDrop={handleFileDrop}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '10px' }}>
              <span style={{ fontSize: '10px', fontWeight: '700', letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--work-green)', fontFamily: 'var(--font-inter)', opacity: 0.8 }}>Project Directory</span>
              <div style={{ display: 'flex', gap: '8px', flexShrink: 0 }}>
                <span className="material-icons" onClick={handleOpenFilePicker} style={{ cursor: 'pointer', fontSize: '16px', color: 'var(--work-green)' }} title="Open file from disk">folder_open</span>
                <span className="material-icons" onClick={() => handleCreateItem('file')} style={{ cursor: 'pointer', fontSize: '16px', color: 'var(--primary-color)' }} title="New file">note_add</span>
                <span className="material-icons" onClick={() => handleCreateItem('directory')} style={{ cursor: 'pointer', fontSize: '16px', color: 'var(--primary-color)' }} title="New folder">create_new_folder</span>
                <span className="material-icons" onClick={loadWorkspaceTree} style={{ cursor: 'pointer', fontSize: '16px', color: 'var(--primary-color)' }} title="Refresh">refresh</span>
              </div>
            </div>

            {/* Drop zone hint — only shows when nothing is mounted yet */}
            {isDraggingOver && (
              <div style={{
                position: 'absolute', inset: 0, display: 'flex', alignItems: 'center',
                justifyContent: 'center', pointerEvents: 'none', zIndex: 10,
                color: 'var(--work-green)', fontSize: '13px', fontFamily: 'var(--font-inter)',
                gap: '8px'
              }}>
                <span className="material-icons" style={{ fontSize: '20px' }}>download</span>
                Drop to open
              </div>
            )}

            {/* Mount controls */}
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '10px' }}>
              <input
                type="text"
                value={workspacePath}
                onChange={(e) => setWorkspacePath(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') loadWorkspaceTree(); }}
                placeholder="Paste folder path, then press Enter"
                style={{
                  flex: 1, background: 'transparent', border: 'none',
                  borderBottom: '1px solid var(--accent-purple)',
                  color: 'var(--work-green)', padding: '3px 0',
                  fontSize: '11px', fontFamily: 'var(--font-inter)', outline: 'none'
                }}
              />
              <span
                className="material-icons"
                onClick={loadWorkspaceTree}
                title="Mount path"
                style={{ fontSize: '16px', color: 'var(--work-green)', cursor: 'pointer', flexShrink: 0 }}
              >
                login
              </span>
            </div>

            <div style={{ overflowY: 'auto', flex: 1 }}>
              {fileTree ? renderFileTree(fileTree) : <div style={{ color: 'var(--text-dim)', fontSize: '14px' }}>Scanning hypervisor...</div>}
            </div>
          </div>

          {/* FILE TREE | EDITOR RESIZE HANDLE */}
          <div
            onMouseDown={(e) => startResize(e, 'fileTree')}
            style={{
              width: '4px', flexShrink: 0, cursor: 'col-resize',
              background: 'var(--border-dashed-color, rgba(255,0,127,0.15))',
              transition: 'background 0.15s'
            }}
            onMouseEnter={e => e.currentTarget.style.background = 'var(--work-green)'}
            onMouseLeave={e => e.currentTarget.style.background = 'var(--border-dashed-color, rgba(255,0,127,0.15))'}
            title="Drag to resize"
          />

          <div className="workspace-pane" style={{ flex: 1, borderRight: 'none', borderLeft: 'none', padding: 0, minWidth: 0 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '15px', borderBottom: 'var(--border-dashed)' }}>
              <h3 className="workspace-pane-header" style={{ margin: 0, border: 'none', color: 'var(--work-green)' }}>Artifact Viewer</h3>
              {activeFilePath && (
                <button
                  onClick={handleSaveFile}
                  style={{
                    background: 'transparent',
                    color: 'var(--work-green)',
                    border: 'none',
                    padding: '6px 15px',
                    cursor: 'pointer',
                    fontSize: '12px',
                    fontWeight: 'bold',
                    textTransform: 'uppercase',
                    letterSpacing: '1px'
                  }}
                  onMouseEnter={(e) => e.target.style.textShadow = '0 0 8px var(--work-green)'}
                  onMouseLeave={(e) => e.target.style.textShadow = 'none'}
                >
                  SAVE
                </button>
              )}
            </div>
            <div style={{ flex: 1, height: 'calc(100% - 60px)' }}>
              <Editor
                height="100%"
                language={editorLanguage}
                value={workspaceCode}
                theme={getMonacoTheme()}
                beforeMount={handleEditorWillMount}
                options={{
                  fontSize: 16,
                  minimap: { enabled: false },
                  scrollbar: {
                    vertical: 'auto',
                    horizontal: 'auto'
                  },
                  fontFamily: 'Inter, sans-serif',
                  automaticLayout: true
                }}
              />
            </div>
          </div>

          {/* EDITOR | CHAT RESIZE HANDLE */}
          <div
            onMouseDown={(e) => startResize(e, 'chat')}
            style={{
              width: '4px', flexShrink: 0, cursor: 'col-resize',
              background: 'var(--border-dashed-color, rgba(255,0,127,0.15))',
              transition: 'background 0.15s'
            }}
            onMouseEnter={e => e.currentTarget.style.background = 'var(--primary-color)'}
            onMouseLeave={e => e.currentTarget.style.background = 'var(--border-dashed-color, rgba(255,0,127,0.15))'}
            title="Drag to resize"
          />

          <div className="workspace-pane" style={{ width: chatPanelWidth, flexShrink: 0, borderRight: 'none', padding: 0, overflow: 'hidden' }}>
            {activePersona ? renderChatInterface() : (
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: 'var(--text-dim)' }}>
                Select a persona to mount Neural Uplink.
              </div>
            )}
          </div>
        </div>
      ) : (
        <div className="chat-area">
          {currentView === 'group' ? (
            renderGroupChat()
          ) : currentView === 'studio' ? (
            <div className="studio-view glass-panel animate-fade-in" style={{ height: '100%', display: 'flex', flexDirection: 'column', padding: '40px', overflowY: 'auto' }}>
              <h1 style={{ color: 'var(--work-green)', marginBottom: '10px' }}>THE WORKSHOP</h1>
              <p style={{ opacity: 0.8, marginBottom: '30px' }}>Refine your AI companions and their collective intelligence.</p>

              <div style={{ display: 'flex', gap: '30px', flexWrap: 'wrap' }}>
                <div style={{ flex: '1 1 400px', display: 'flex', flexDirection: 'column', gap: '15px' }}>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', borderBottom: '1px solid rgba(255,0,127,0.3)', paddingBottom: '8px' }}>
                    <h3 style={{ color: '#00e5ff', margin: 0 }}>{newPersona.originalKey ? 'Edit Persona' : 'Forge Persona'}</h3>
                    <button
                      className="header-button"
                      style={{ fontSize: '12px', padding: '4px 12px' }}
                      onClick={() => setNewPersona({ originalKey: "", key: "", name: "", avatar: "🤖", tagline: "", system_prompt: "", on_demand_files: [], access_code: "", om_enabled: true, om_turn_threshold: 5, deep_memory_enabled: false })}
                    >
                      ➕ NEW PERSONA
                    </button>
                  </div>

                  <input type="text" className="chat-input" placeholder="Name (e.g. Neo)"
                    value={newPersona.name} onChange={e => {
                      const name = e.target.value;
                      const updates = { name };
                      if (!newPersona.originalKey) {
                        updates.key = name.toLowerCase().replace(/[^a-z0-9]/g, '_').replace(/_+/g, '_').replace(/^_|_$/g, '');
                      }
                      setNewPersona({ ...newPersona, ...updates });
                    }} />

                  <input type="text" className="chat-input" placeholder="Emoji / Avatar (e.g. 🕶️)"
                    value={newPersona.avatar} onChange={e => setNewPersona({ ...newPersona, avatar: e.target.value })} />

                  <input type="text" className="chat-input" placeholder="Tagline (e.g. The chosen one.)"
                    value={newPersona.tagline} onChange={e => setNewPersona({ ...newPersona, tagline: e.target.value })} />

                  <textarea className="chat-input" placeholder="System Prompt / Core Directives..." rows={10} style={{ resize: 'vertical' }}
                    value={newPersona.system_prompt} onChange={e => setNewPersona({ ...newPersona, system_prompt: e.target.value })} />

                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px', borderBottom: '1px solid rgba(255,0,127,0.3)', paddingBottom: '8px', marginTop: '10px' }}>
                    <h3 style={{ color: '#ff007f', margin: 0 }}>Knowledge</h3>
                    <span className="material-icons" style={{ fontSize: '16px', opacity: 0.7 }} title="Add files for your Persona to reference">info_outline</span>
                  </div>

                  <div
                    className="glass-panel"
                    style={{
                      padding: '16px',
                      borderRadius: '8px',
                      display: 'flex',
                      flexDirection: 'column',
                      gap: '10px',
                      background: 'rgba(0,0,0,0.2)'
                    }}
                  >
                    <input type="file" id="on_demand_upload" style={{ display: 'none' }} multiple onChange={async (e) => {
                      const files = Array.from(e.target.files);
                      if (!files.length) return;
                      try {
                        const newPaths = [];
                        for (const file of files) {
                          const res = await api.uploadFile(file);
                          newPaths.push(res.path);
                        }
                        setNewPersona(prev => ({ ...prev, on_demand_files: [...prev.on_demand_files, ...newPaths] }));
                      } catch (err) {
                        alert("Upload failed: " + err.message);
                      }
                      e.target.value = null;
                    }} />

                    {newPersona.on_demand_files && newPersona.on_demand_files.length > 0 && (
                      <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                        {newPersona.on_demand_files.map((filePath, idx) => (
                          <div key={idx} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '10px', overflow: 'hidden' }}>
                              <span className="material-icons" style={{ color: '#00e5ff' }}>insert_drive_file</span>
                              <span style={{ color: 'var(--text-primary)', whiteSpace: 'nowrap', textOverflow: 'ellipsis', overflow: 'hidden' }}>
                                {filePath.split(/[/\\]/).pop()}
                              </span>
                            </div>
                            <button
                              className="material-icons"
                              style={{ background: 'transparent', border: 'none', color: '#af52ff', cursor: 'pointer', padding: '4px' }}
                              title="Remove Document"
                              onClick={(e) => {
                                e.preventDefault();
                                const newArr = [...newPersona.on_demand_files];
                                newArr.splice(idx, 1);
                                setNewPersona({ ...newPersona, on_demand_files: newArr });
                              }}
                            >
                              close
                            </button>
                          </div>
                        ))}
                      </div>
                    )}

                    <button
                      style={{ background: 'transparent', border: 'none', color: 'var(--text-color)', opacity: 0.8, cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '8px', padding: 0 }}
                      onClick={(e) => {
                        e.preventDefault();
                        document.getElementById('on_demand_upload').click();
                      }}
                    >
                      <span style={{ fontSize: '18px' }}>+</span> Add files for your Persona to reference
                    </button>
                  </div>

                  {/* OBSERVATIONAL MEMORY SETTINGS */}
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px', borderBottom: '1px solid rgba(255,0,127,0.3)', paddingBottom: '8px', marginTop: '10px' }}>
                    <h3 style={{ color: '#ff007f', margin: 0 }}>Observational Memory</h3>
                    <span className="material-icons" style={{ fontSize: '16px', opacity: 0.7 }} title="Turn off to conserve API tokens, dial up to process memory less frequently.">info_outline</span>
                  </div>
                  <div className="glass-panel" style={{ padding: '16px', borderRadius: '8px', display: 'flex', flexDirection: 'column', gap: '15px', background: 'rgba(0,0,0,0.2)' }}>
                    <label style={{ display: 'flex', alignItems: 'center', gap: '10px', cursor: 'pointer', color: 'var(--text-primary)' }}>
                      <input
                        type="checkbox"
                        checked={newPersona.om_enabled !== false}
                        onChange={(e) => setNewPersona({ ...newPersona, om_enabled: e.target.checked })}
                        style={{ transform: 'scale(1.2)', accentColor: 'var(--primary-color)' }}
                      />
                      Enable Autonomous Reflection (Token Consuming)
                    </label>
                    <label style={{ display: 'flex', flexDirection: 'column', gap: '5px', color: 'var(--text-primary)', opacity: newPersona.om_enabled !== false ? 1 : 0.5 }}>
                      <span style={{ fontSize: '12px', opacity: 0.8 }}>Reflection Frequency Threshold (in conversation turns)</span>
                      <input
                        type="number"
                        className="chat-input"
                        min="1" max="100"
                        value={newPersona.om_turn_threshold !== undefined ? newPersona.om_turn_threshold : 5}
                        disabled={newPersona.om_enabled === false}
                        onChange={(e) => setNewPersona({ ...newPersona, om_turn_threshold: parseInt(e.target.value) || 5 })}
                        style={{ width: '120px', padding: '8px' }}
                      />
                    </label>
                  </div>

                  {/* DEEP MEMORY SETTINGS */}
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px', borderBottom: '1px solid rgba(255,0,127,0.3)', paddingBottom: '8px', marginTop: '10px' }}>
                    <h3 style={{ color: '#ff007f', margin: 0, textTransform: 'uppercase' }}>DEEP MEMORY</h3>
                    <span className="material-icons" style={{ fontSize: '16px', opacity: 0.7 }} title="Associative memory with emotional weighting, temporal decay, and involuntary recall chains. Adapted from emergence-kit.">info_outline</span>
                  </div>
                  <div className="glass-panel" style={{ padding: '16px', borderRadius: '8px', display: 'flex', flexDirection: 'column', gap: '15px', background: 'rgba(0,0,0,0.2)' }}>
                    <label style={{ display: 'flex', alignItems: 'center', gap: '10px', cursor: 'pointer', color: 'var(--text-primary)' }}>
                      <input
                        type="checkbox"
                        id="deep_memory_toggle"
                        checked={!!newPersona.deep_memory_enabled}
                        onChange={(e) => {
                          const val = e.target.checked;
                          setNewPersona(prev => ({ ...prev, deep_memory_enabled: val }));
                        }}
                        style={{
                          transform: 'scale(1.4)',
                          accentColor: '#00e5ff',
                          cursor: 'pointer',
                          border: '1px solid #00e5ff !important'
                        }}
                      />
                      Enable Associative Memory Graph
                    </label>
                    <span style={{ fontSize: '11px', opacity: 0.6, lineHeight: '1.5' }}>
                      Memories form weighted connections based on shared tags, emotions, and content.
                      Old unimportant memories decay over time. Important ones are protected.
                      The persona gains a sense of time between conversations.
                    </span>
                  </div>

                  {/* LOREBOOK */}
                  {newPersona.originalKey && (
                    <>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', borderBottom: '1px solid rgba(255,0,127,0.3)', paddingBottom: '8px', marginTop: '10px' }}>
                        <h3 style={{ color: '#ff007f', margin: 0 }}>Lorebook</h3>
                        <span className="material-icons" style={{ fontSize: '16px', opacity: 0.7 }} title="Add background lore, world info, or character history. The system intelligently surfaces relevant knowledge during conversations.">info_outline</span>
                      </div>

                      <div className="glass-panel" style={{ padding: '16px', borderRadius: '8px', display: 'flex', flexDirection: 'column', gap: '12px', background: 'rgba(0,0,0,0.2)' }}>
                        <input
                          type="text"
                          className="chat-input"
                          placeholder="Entry title (e.g. The Dark Council)"
                          value={loreForm.title}
                          onChange={e => setLoreForm(prev => ({ ...prev, title: e.target.value }))}
                        />
                        <textarea
                          className="chat-input"
                          placeholder="Write lore, history, world info, or character backstory here..."
                          rows={5}
                          style={{ resize: 'vertical' }}
                          value={loreForm.content}
                          onChange={e => setLoreForm(prev => ({ ...prev, content: e.target.value }))}
                        />
                        <div style={{ display: 'flex', gap: '8px' }}>
                          <button
                            className="header-button"
                            style={{ flex: 1, fontSize: '12px', padding: '6px 12px' }}
                            disabled={loreSaving || !loreForm.title.trim() || !loreForm.content.trim()}
                            onClick={async () => {
                              if (!loreForm.title.trim() || !loreForm.content.trim()) return;
                              setLoreSaving(true);
                              try {
                                if (loreEditId) {
                                  await api.updateLoreEntry(newPersona.originalKey, loreEditId, USERNAME, loreForm.title, loreForm.content, { universal: apiKeys.universal });
                                } else {
                                  await api.createLoreEntry(newPersona.originalKey, USERNAME, loreForm.title, loreForm.content, { universal: apiKeys.universal });
                                }
                                const entries = await api.fetchLoreEntries(newPersona.originalKey, USERNAME);
                                setLoreEntries(entries);
                                setLoreForm({ title: '', content: '' });
                                setLoreEditId(null);
                              } catch (err) {
                                alert('Failed to save lore: ' + err.message);
                              }
                              setLoreSaving(false);
                            }}
                          >
                            {loreSaving ? '⏳ Saving...' : loreEditId ? '💾 Update Entry' : '➕ Add Entry'}
                          </button>
                          {loreEditId && (
                            <button
                              className="header-button"
                              style={{ fontSize: '12px', padding: '6px 12px', borderColor: 'rgba(255,255,255,0.3)', color: 'rgba(255,255,255,0.5)' }}
                              onClick={() => { setLoreForm({ title: '', content: '' }); setLoreEditId(null); }}
                            >
                              Cancel
                            </button>
                          )}
                        </div>
                      </div>

                      {/* Lore Entry List */}
                      {loreLoading ? (
                        <div style={{ opacity: 0.5, fontSize: '13px', textAlign: 'center', padding: '10px' }}>Loading entries...</div>
                      ) : loreEntries.length > 0 && (
                        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', maxHeight: '300px', overflowY: 'auto' }}>
                          {loreEntries.map(entry => (
                            <div
                              key={entry.id}
                              className="glass-panel"
                              style={{ padding: '12px', borderRadius: '8px', background: 'rgba(0,0,0,0.2)', display: 'flex', flexDirection: 'column', gap: '6px', border: loreEditId === entry.id ? '1px solid var(--primary-color)' : '1px solid transparent' }}
                            >
                              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                                <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                                  <span style={{ fontWeight: 'bold', fontSize: '13px', color: '#00e5ff' }}>{entry.title}</span>
                                  {!entry.processed ? (
                                    <span style={{
                                      fontSize: '9px',
                                      background: 'rgba(0, 229, 255, 0.1)',
                                      color: '#00e5ff',
                                      border: '1px solid rgba(0, 229, 255, 0.3)',
                                      padding: '2px 8px',
                                      borderRadius: '4px',
                                      display: 'flex',
                                      alignItems: 'center',
                                      gap: '4px'
                                    }}>
                                      <span className="material-icons glow-pulse" style={{ fontSize: '10px' }}>sync</span>
                                      RECALIBRATING GRAPH
                                    </span>
                                  ) : (
                                    <span style={{
                                      fontSize: '9px',
                                      background: 'rgba(0, 204, 102, 0.1)',
                                      color: '#00cc66',
                                      border: '1px solid rgba(0, 204, 102, 0.3)',
                                      padding: '2px 8px',
                                      borderRadius: '4px',
                                      display: 'flex',
                                      alignItems: 'center',
                                      gap: '4px'
                                    }}>
                                      <span className="material-icons" style={{ fontSize: '10px', color: 'inherit' }}>done_all</span>
                                      KNOWLEDGE SYNCED
                                    </span>
                                  )}
                                </div>
                                <div style={{ display: 'flex', gap: '6px' }}>
                                  <button
                                    className="material-icons"
                                    style={{ background: 'transparent', border: 'none', color: '#00e5ff', cursor: 'pointer', fontSize: '16px', padding: '2px' }}
                                    title="Edit"
                                    onClick={() => {
                                      setLoreEditId(entry.id);
                                      setLoreForm({ title: entry.title, content: entry.content });
                                    }}
                                  >edit</button>
                                  <button
                                    className="material-icons"
                                    style={{ background: 'transparent', border: 'none', color: '#af52ff', cursor: 'pointer', fontSize: '16px', padding: '2px' }}
                                    title="Delete"
                                    onClick={async () => {
                                      if (!window.confirm(`Delete "${entry.title}"? This cannot be undone.`)) return;
                                      await api.deleteLoreEntry(newPersona.originalKey, entry.id, USERNAME);
                                      const entries = await api.fetchLoreEntries(newPersona.originalKey, USERNAME);
                                      setLoreEntries(entries);
                                      if (loreEditId === entry.id) { setLoreEditId(null); setLoreForm({ title: '', content: '' }); }
                                    }}
                                  >delete_forever</button>
                                </div>
                              </div>
                              <div style={{ fontSize: '12px', opacity: 0.6, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                                {entry.content.slice(0, 100)}{entry.content.length > 100 ? '...' : ''}
                              </div>
                            </div>
                          ))}
                        </div>
                      )}
                    </>
                  )}

                  <button className="header-button" onClick={async () => {
                    if (!newPersona.name || !newPersona.system_prompt) return alert("Name and Prompt required.");
                    try {
                      let finalKey = newPersona.key;
                      if (!finalKey) {
                        finalKey = newPersona.name.toLowerCase().replace(/[^a-z0-9]/g, '_').replace(/_+/g, '_').replace(/^_|_$/g, '');
                      }
                      const payload = { ...newPersona };
                      delete payload.originalKey; // Strip react-only state to prevent Pydantic 444 Blackhole

                      await api.createPersona({ username: USERNAME, ...payload, original_key: newPersona.originalKey, key: finalKey });
                      const updated = await api.fetchPersonas(USERNAME);
                      setPersonas(updated);
                      // Do not reset if it was an update, to keep the form populated
                      if (!newPersona.originalKey) {
                        setNewPersona({ originalKey: "", key: "", name: "", avatar: "🤖", tagline: "", system_prompt: "", on_demand_files: [], access_code: "", om_enabled: true, om_turn_threshold: 5, deep_memory_enabled: false });
                      } else {
                        // Update the originalKey with the potentially new key
                        setNewPersona(prev => ({ ...prev, originalKey: finalKey, key: finalKey }));
                      }
                      alert(newPersona.originalKey ? `${newPersona.name} updated!` : `${newPersona.name} forged!`);
                    } catch (err) {
                      alert("Error saving persona: " + err.message);
                    }
                  }}>
                    {newPersona.originalKey ? '💾 UPDATE PERSONA' : '🔨 FORGE PERSONA'}
                  </button>
                </div>

                <div style={{ flex: '1 1 400px', display: 'flex', flexDirection: 'column', gap: '15px' }}>
                  <h3 style={{ borderBottom: '1px solid rgba(255,0,127,0.3)', paddingBottom: '8px', color: '#b060ff' }}>Existing Personas</h3>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                    {Object.entries(personas).map(([key, p]) => (
                      <div
                        key={key}
                        className="glass-panel persona-list-item"
                        style={{
                          padding: '15px',
                          display: 'flex',
                          alignItems: 'center',
                          gap: '15px',
                          cursor: 'pointer',
                          transition: 'var(--transition-fast)'
                        }}
                        onMouseEnter={(e) => e.currentTarget.style.transform = 'scale(1.02)'}
                        onMouseLeave={(e) => e.currentTarget.style.transform = 'scale(1)'}
                        onClick={() => {
                          setNewPersona({
                            originalKey: key,
                            key: key,
                            name: p.name,
                            avatar: p.avatar,
                            tagline: p.tagline,
                            system_prompt: p.system_prompt || '',
                            on_demand_files: p.on_demand_files || [],
                            access_code: p.access_code || '',
                            om_enabled: p.om_enabled !== false,
                            om_turn_threshold: p.om_turn_threshold !== undefined ? p.om_turn_threshold : 5,
                            deep_memory_enabled: !!p.deep_memory_enabled
                          });
                        }}
                      >
                        <div style={{ fontSize: '24px' }}>{p.avatar}</div>
                        <div>
                          <div style={{ fontWeight: 'bold', color: 'var(--text-primary)' }}>{p.name}</div>
                          <div style={{ fontSize: '12px', opacity: 0.7 }}>{p.is_custom ? '🛠️ Custom' : '📦 Built-in'}</div>
                        </div>
                        <div style={{ marginLeft: 'auto' }}>
                          <span
                            className="material-icons"
                            style={{ color: 'var(--primary-color)', fontSize: '20px' }}
                            title="Edit Persona"
                          >
                            edit
                          </span>
                        </div>
                        {p.is_custom && (
                          <div style={{ marginLeft: '10px' }}>
                            <span
                              className="material-icons"
                              style={{ color: '#af52ff', fontSize: '20px' }}
                              title="Delete Persona"
                              onClick={async (e) => {
                                e.stopPropagation();
                                if (window.confirm(`Are you sure you want to incinerate ${p.name}? This cannot be undone.`)) {
                                  try {
                                    const success = await api.deletePersona(key, USERNAME);
                                    if (success) {
                                      const updated = await api.fetchPersonas(USERNAME);
                                      setPersonas(updated);
                                      if (activePersona === key) {
                                        setActivePersona(Object.keys(updated)[0] || null);
                                      }
                                      alert(`${p.name} has been deleted.`);
                                    } else {
                                      alert("Incineration failed.");
                                    }
                                  } catch (err) {
                                    alert("Error: " + err.message);
                                  }
                                }
                              }}
                            >
                              delete_forever
                            </span>
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            </div>
          ) : activePersona ? (
            renderChatInterface()
          ) : (
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: 'var(--text-dim)' }}>
              Select a persona to begin initialization.
            </div>
          )}
        </div>
      )}

      {/* SETTINGS MODAL */}
      {showSettings && (() => {
        const S = {
          overlay: {
            position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
            backgroundColor: 'rgba(0,0,0,0.75)', zIndex: 100,
            display: 'flex', alignItems: 'center', justifyContent: 'center'
          },
          shell: {
            width: '860px', height: '580px',
            background: appTheme === 'q-light' ? '#fff' : appTheme === 'q-dark' ? '#111113' : '#0a0a0a',
            border: appTheme === 'void' ? '1px solid #1a0a2a' : `1px solid ${appTheme === 'q-dark' ? '#1f1f24' : '#d1d3d6'}`,
            borderRadius: '8px',
            display: 'flex',
            overflow: 'hidden',
            boxShadow: '0 8px 32px rgba(0,0,0,0.4)'
          },
          nav: {
            width: '200px',
            background: appTheme === 'q-light' ? '#f2f3f5' : appTheme === 'q-dark' ? '#080809' : '#050505',
            borderRight: appTheme === 'void' ? '1px solid #1a0a2a' : `1px solid ${appTheme === 'q-dark' ? '#1f1f24' : '#e3e5e8'}`,
            display: 'flex', flexDirection: 'column',
            padding: '20px 0',
            flexShrink: 0
          },
          categoryLabel: {
            fontSize: '11px', fontWeight: '700',
            letterSpacing: '0.08em', textTransform: 'uppercase',
            color: appTheme === 'q-light' ? '#6d6f78' : appTheme === 'q-dark' ? '#72767d' : 'rgba(0,204,102,0.5)',
            padding: '12px 16px 4px 16px',
            fontFamily: 'Inter, sans-serif'
          },
          navItem: (id) => ({
            padding: '8px 16px',
            cursor: 'pointer',
            fontSize: '14px',
            fontFamily: 'Inter, sans-serif',
            borderRadius: '4px',
            margin: '1px 8px',
            transition: 'background 0.1s ease',
            background: settingsSection === id
              ? (appTheme === 'void' ? 'rgba(255,0,127,0.12)' : 'rgba(88,101,242,0.2)')
              : 'transparent',
            color: settingsSection === id
              ? (appTheme === 'void' ? '#ff007f' : '#5865f2')
              : (appTheme === 'q-light' ? '#4e5058' : appTheme === 'q-dark' ? '#dcddde' : '#00cc66'),
            fontWeight: settingsSection === id ? '600' : '400'
          }),
          content: {
            flex: 1, overflowY: 'auto', padding: '28px 32px',
            display: 'flex', flexDirection: 'column', gap: '0'
          },
          sectionTitle: {
            fontSize: '20px', fontWeight: '700',
            color: appTheme === 'q-light' ? '#2e3338' : appTheme === 'q-dark' ? '#fff' : '#ff007f',
            fontFamily: 'Inter, sans-serif',
            marginBottom: '4px'
          },
          sectionSub: {
            fontSize: '13px', opacity: 0.8,
            fontFamily: 'Inter, sans-serif',
            marginBottom: '24px',
            color: appTheme === 'q-light' ? '#4e5058' : appTheme === 'q-dark' ? '#b5bac1' : '#b060ff'
          },
          divider: {
            height: '1px',
            background: appTheme === 'q-dark' ? '#3f4147' : appTheme === 'q-light' ? '#e3e5e8' : '#1a0a2a',
            margin: '20px 0'
          },
          label: {
            fontSize: '11px', fontWeight: '700', letterSpacing: '0.06em',
            textTransform: 'uppercase', marginBottom: '8px', display: 'block',
            fontFamily: 'Inter, sans-serif',
            color: appTheme === 'q-light' ? '#6d6f78' : appTheme === 'q-dark' ? '#b5bac1' : '#00e5ff'
          },
          input: {
            width: '100%', padding: '8px 12px',
            background: appTheme === 'q-light' ? '#fff' : appTheme === 'q-dark' ? '#020203' : '#0d0d0d',
            border: appTheme === 'q-dark' ? '1px solid #1f1f24' : appTheme === 'q-light' ? '1px solid #d1d3d6' : '1px solid #1a0a2a',
            borderRadius: '4px',
            color: appTheme === 'q-light' ? '#2e3338' : appTheme === 'q-dark' ? '#dcddde' : '#00cc66',
            fontSize: '14px', fontFamily: 'Inter, sans-serif',
            outline: 'none', marginBottom: '16px'
          },
          rowLabel: {
            fontSize: '13px', display: 'flex', justifyContent: 'space-between',
            marginBottom: '6px', fontFamily: 'Inter, sans-serif',
            color: appTheme === 'q-light' ? '#4e5058' : appTheme === 'q-dark' ? '#b5bac1' : '#b060ff'
          }
        };


        const navSection = (id, label) => (
          <div style={S.navItem(id)} onClick={() => setSettingsSection(id)}>{label}</div>
        );

        const renderContent = () => {
          if (settingsSection === 'api') return (
            <div>
              <div style={S.sectionTitle}>API & Routing</div>
              <div style={S.sectionSub}>Model selection, API keys, and custom provider configuration.</div>

              <label style={S.label}>Base Model (Flash)</label>
              <input style={S.input} type="text" placeholder="e.g. google/gemini-3-flash-preview"
                value={advancedOptions.baseModelId}
                onChange={e => setAdvancedOptions(prev => ({ ...prev, baseModelId: e.target.value }))} />

              <label style={S.label}>Expert Model (Pro / Opus)</label>
              <input style={S.input} type="text" placeholder="e.g. google/gemini-3.1-pro-preview"
                value={advancedOptions.expertModelId}
                onChange={e => setAdvancedOptions(prev => ({ ...prev, expertModelId: e.target.value }))} />

              <label style={S.label}>Universal API Key</label>
              <input style={S.input} type="password" placeholder="sk-or-v1-..."
                value={apiKeys.universal}
                onChange={e => setApiKeys({ universal: e.target.value })} />

              <div style={S.divider} />

              <label style={S.label}>Custom Base URL</label>
              <input style={S.input} type="text" placeholder="e.g. http://localhost:11434/v1/chat/completions"
                value={advancedOptions.customBaseUrl}
                onChange={e => setAdvancedOptions(prev => ({ ...prev, customBaseUrl: e.target.value }))} />

              <div style={{ display: 'flex', gap: '16px' }}>
                <div style={{ flex: 1 }}>
                  <label style={S.label}>Provider Format</label>
                  <select style={{...S.input, appearance: 'menulist', cursor: 'pointer'}}
                    value={advancedOptions.customProviderType}
                    onChange={e => setAdvancedOptions(prev => ({ ...prev, customProviderType: e.target.value }))}>
                    <option value="openai">OpenAI-Compatible</option>
                    <option value="anthropic">Anthropic-Compatible</option>
                  </select>
                </div>
                <div style={{ flex: 1 }}>
                  <label style={S.label}>Auth Header</label>
                  <input style={S.input} type="text" placeholder="Authorization"
                    value={advancedOptions.customAuthHeaderName}
                    onChange={e => setAdvancedOptions(prev => ({ ...prev, customAuthHeaderName: e.target.value }))} />
                </div>
                <div style={{ flex: 1 }}>
                  <label style={S.label}>Auth Prefix</label>
                  <input style={S.input} type="text" placeholder="Bearer "
                    value={advancedOptions.customAuthPrefix}
                    onChange={e => setAdvancedOptions(prev => ({ ...prev, customAuthPrefix: e.target.value }))} />
                </div>
              </div>
            </div>
          );

          if (settingsSection === 'params') return (
            <div>
              <div style={S.sectionTitle}>Parameters</div>
              <div style={S.sectionSub}>Tune generation behavior per session.</div>

              <label style={S.label}>Reasoning Level</label>
              <select style={{...S.input, appearance: 'menulist', cursor: 'pointer', marginBottom: '20px'}}
                value={advancedOptions.thinkingLevel}
                onChange={e => setAdvancedOptions(prev => ({ ...prev, thinkingLevel: e.target.value }))}>
                <option value="Off">Off</option>
                <option value="Low">Low</option>
                <option value="Medium">Medium</option>
                <option value="High">High</option>
              </select>

              {[
                ['Temperature', 'temperature', 0, 2, 0.1],
                ['Top P', 'topP', 0, 1, 0.05],
                ['Top K', 'topK', 0, 100, 1],
                ['Max Tokens', 'maxTokens', 1, 8192, 1],
                ['Presence Penalty', 'presencePenalty', -2, 2, 0.1],
                ['Frequency Penalty', 'frequencyPenalty', -2, 2, 0.1],
              ].map(([name, key, min, max, step]) => (
                <div key={key} style={{ marginBottom: '18px' }}>
                  <div style={S.rowLabel}>
                    <span>{name}</span>
                    <span style={{ fontWeight: '600', color: appTheme === 'void' ? '#ff007f' : '#5865f2' }}>
                      {typeof advancedOptions[key] === 'number' && step < 1
                        ? advancedOptions[key].toFixed(2)
                        : advancedOptions[key]}
                    </span>
                  </div>
                  <input type="range" min={min} max={max} step={step}
                    value={advancedOptions[key]}
                    onChange={e => {
                      const v = step < 1 ? parseFloat(e.target.value) : parseInt(e.target.value);
                      setAdvancedOptions(prev => ({ ...prev, [key]: v }));
                    }} />
                </div>
              ))}
            </div>
          );

          if (settingsSection === 'appearance') return (
            <div>
              <div style={S.sectionTitle}>Appearance</div>
              <div style={S.sectionSub}>Choose how Q looks. Your selection is saved automatically.</div>

              <div style={{ display: 'flex', gap: '16px', marginTop: '8px' }}>
                {[
                  { id: 'void', label: 'Void', desc: 'The original. Hot pink neon on pure black.', preview: ['#000000', '#ff007f', '#00cc66'] },
                  { id: 'q-dark', label: 'Dark', desc: 'Clean Discord-style dark. Easy on the eyes.', preview: ['#313338', '#5865f2', '#dcddde'] },
                  { id: 'q-light', label: 'Light', desc: 'Light mode for bright environments.', preview: ['#f2f3f5', '#5865f2', '#2e3338'] },
                ].map(({ id, label, desc, preview }) => (
                  <div key={id}
                    onClick={() => setAppTheme(id)}
                    style={{
                      flex: 1, borderRadius: '8px', overflow: 'hidden', cursor: 'pointer',
                      border: appTheme === id
                        ? `2px solid ${id === 'void' ? '#ff007f' : '#5865f2'}`
                        : `2px solid ${appTheme === 'q-light' ? '#d1d3d6' : '#3f4147'}`,
                      transition: 'border-color 0.15s ease',
                    }}>
                    {/* Preview swatch */}
                    <div style={{ height: '80px', background: preview[0], display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '8px' }}>
                      <div style={{ width: '28px', height: '28px', borderRadius: '50%', background: preview[1] }} />
                      <div style={{ width: '14px', height: '14px', borderRadius: '50%', background: preview[2] }} />
                    </div>
                    <div style={{
                      padding: '12px',
                      background: appTheme === 'q-dark' ? '#232428' : appTheme === 'q-light' ? '#f2f3f5' : '#0d0d0d'
                    }}>
                      <div style={{ fontWeight: '700', fontSize: '14px', fontFamily: 'Inter, sans-serif',
                        color: appTheme === id ? (id === 'void' ? '#ff007f' : '#5865f2') : (appTheme === 'q-light' ? '#2e3338' : '#dcddde')
                      }}>{label}</div>
                      <div style={{ fontSize: '12px', opacity: 0.6, fontFamily: 'Inter, sans-serif', marginTop: '4px',
                        color: appTheme === 'q-light' ? '#4e5058' : '#b5bac1'
                      }}>{desc}</div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          );

          if (settingsSection === 'maintenance') return (
            <div>
              <div style={S.sectionTitle}>Maintenance</div>
              <div style={S.sectionSub}>Danger zone. These actions are permanent.</div>

              <div style={{ display: 'flex', flexDirection: 'column', gap: '12px', maxWidth: '400px' }}>
                <div>
                  <div style={{ fontSize: '13px', fontWeight: '600', fontFamily: 'Inter, sans-serif',
                    color: appTheme === 'q-light' ? '#2e3338' : appTheme === 'q-dark' ? '#dcddde' : '#00cc66', marginBottom: '4px' }}>
                    Chat History
                  </div>
                  <div style={{ fontSize: '12px', fontFamily: 'Inter, sans-serif', marginBottom: '10px',
                    color: appTheme === 'q-light' ? '#4e5058' : appTheme === 'q-dark' ? '#b5bac1' : '#b060ff', opacity: 0.85 }}>
                    Clears all messages for the active persona. Cannot be undone.
                  </div>
                  <button onClick={async () => {
                    if (activePersona) {
                      const ok = await api.clearChatHistory(activePersona, USERNAME);
                      if (ok) { setChatHistory([]); }
                    }
                  }} style={{
                    padding: '4px 0', cursor: 'pointer',
                    background: 'transparent', border: 'none', borderBottom: '1px solid rgba(237,66,69,0.5)',
                    color: '#ed4245', fontFamily: 'Inter, sans-serif', fontSize: '13px', fontWeight: '600'
                  }}>Clear Current Chat &rarr;</button>
                </div>

                <div style={S.divider} />

                <div>
                  <div style={{ fontSize: '13px', fontWeight: '600', fontFamily: 'Inter, sans-serif',
                    color: appTheme === 'q-light' ? '#2e3338' : appTheme === 'q-dark' ? '#dcddde' : '#00cc66', marginBottom: '4px' }}>
                    Vector Memory
                  </div>
                  <div style={{ fontSize: '12px', fontFamily: 'Inter, sans-serif', marginBottom: '10px',
                    color: appTheme === 'q-light' ? '#4e5058' : appTheme === 'q-dark' ? '#b5bac1' : '#b060ff', opacity: 0.85 }}>
                    Wipes all semantic memories, summaries, observations, and Zettel data for the active persona.
                  </div>
                  <button onClick={async () => {
                    if (activePersona) await api.wipePersonaMemories(activePersona, USERNAME);
                  }} style={{
                    padding: '4px 0', cursor: 'pointer',
                    background: 'transparent', border: 'none', borderBottom: '1px solid rgba(237,66,69,0.5)',
                    color: '#ed4245', fontFamily: 'Inter, sans-serif', fontSize: '13px', fontWeight: '600'
                  }}>Wipe Vector Memory &rarr;</button>
                </div>

                <div style={S.divider} />

                <div>
                  <div style={{ fontSize: '13px', fontWeight: '600', fontFamily: 'Inter, sans-serif',
                    color: appTheme === 'q-light' ? '#2e3338' : appTheme === 'q-dark' ? '#dcddde' : '#00cc66', marginBottom: '4px' }}>
                    Semantic Firewall
                  </div>
                  <div style={{ fontSize: '12px', fontFamily: 'Inter, sans-serif', marginBottom: '10px',
                    color: appTheme === 'q-light' ? '#4e5058' : appTheme === 'q-dark' ? '#b5bac1' : '#b060ff', opacity: 0.85 }}>
                    Toggle intent-based prompt injection blocking. Bypass at your own risk.
                  </div>
                  <button onClick={() => setAdvancedOptions(prev => ({ ...prev, bypassFirewall: !prev.bypassFirewall }))}
                    style={{
                      padding: '4px 0', cursor: 'pointer',
                      background: 'transparent', border: 'none',
                      borderBottom: advancedOptions.bypassFirewall ? '1px solid rgba(237,66,69,0.5)' : '1px solid rgba(88,101,242,0.4)',
                      color: advancedOptions.bypassFirewall ? '#ed4245' : (appTheme === 'void' ? '#00cc66' : '#5865f2'),
                      fontFamily: 'Inter, sans-serif', fontSize: '13px', fontWeight: '600'
                    }}>
                    {advancedOptions.bypassFirewall ? '🔓 Firewall Bypassed' : '🔒 Firewall Enforced'}
                  </button>
                </div>
              </div>
            </div>
          );

          if (settingsSection === 'profile') return (
            <div>
              <div style={S.sectionTitle}>Profile</div>
              <div style={S.sectionSub}>Customize how you appear in conversations.</div>

              <label style={S.label}>Your Avatar</label>
              <div style={{ display: 'flex', alignItems: 'center', gap: '16px', marginBottom: '24px' }}>
                <div style={{ fontSize: '48px', lineHeight: 1 }}>{userAvatar}</div>
                <div style={{ flex: 1 }}>
                  <input
                    style={S.input}
                    type="text"
                    placeholder="Paste any emoji here"
                    value={userAvatar}
                    onChange={e => {
                      const val = e.target.value || '👩‍💻';
                      setUserAvatar(val);
                      localStorage.setItem('q_user_avatar', val);
                    }}
                  />
                  <div style={{ fontSize: '12px', fontFamily: 'Inter, sans-serif', opacity: 0.55 }}>
                    Any emoji works — paste one from your system emoji picker (Win + . or Cmd + Ctrl + Space)
                  </div>
                </div>
              </div>

              <div style={S.divider} />

              <div style={{ fontSize: '12px', fontFamily: 'Inter, sans-serif', opacity: 0.5 }}>
                Your name in conversations is your USERNAME set at first login.
              </div>
            </div>
          );
        };

        return (
          <div style={S.overlay} onClick={e => { if (e.target === e.currentTarget) setShowSettings(false); }}>
            <div style={S.shell}>
              {/* LEFT NAV */}
              <div style={S.nav}>
                <div style={S.categoryLabel}>General</div>
                {navSection('api', 'API & Routing')}
                {navSection('params', 'Parameters')}
                <div style={S.categoryLabel}>Interface</div>
                {navSection('appearance', 'Appearance')}
                {navSection('profile', 'Profile')}
                <div style={S.categoryLabel}>Danger Zone</div>
                {navSection('maintenance', 'Maintenance')}

                {/* Close at bottom of nav */}
                <div style={{ marginTop: 'auto', padding: '16px 8px 4px 8px' }}>
                  <button onClick={() => setShowSettings(false)} style={{
                    width: '100%', padding: '8px', borderRadius: '4px', cursor: 'pointer',
                    background: 'transparent', border: 'none',
                  color: appTheme === 'q-light' ? '#6d6f78' : appTheme === 'q-dark' ? '#72767d' : 'rgba(0,204,102,0.4)',
                    fontSize: '13px', fontFamily: 'Inter, sans-serif',
                    display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '6px'
                  }}>
                    <span className="material-icons" style={{ fontSize: '16px', color: 'inherit' }}>close</span>
                    Close
                  </button>
                </div>
              </div>

              {/* RIGHT CONTENT */}
              <div style={S.content}>
                {renderContent()}
              </div>
            </div>
          </div>
        );
      })()}

    </div>
  )
}

export default App
