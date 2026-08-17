let currentAgentList = [];

// ---- 增量加载 / 浮动提示 模块级状态（T-meeting-incremental）----
let clientNewestId = null;     // 当前已渲染的最新消息 id（展示用）
let clientOldestId = null;     // 当前已渲染的最旧消息 id（before 游标基准）
let pollCursorId = null;       // 轮询游标：只由「服务端返回的消息」推进，乐观发送不推进（BUG-C 修复）
let insertedIds = new Set();   // 已插入消息 id 集合，去重幂等（AC-2.3）
let loadingOlder = false;      // 触顶加载守卫，防并发重入
let noMoreOlder = false;       // 已到最早历史，停止发起 before_id 请求（风险-B 修复）
let isAtBottom = true;         // 用户是否位于列表最底层
const BOTTOM_THRESHOLD = 4;    // 距底/距顶 ≤4px 视为「在底部」/「触顶」
let bannerTimer = null;        // 浮动提示自动消失计时器句柄

function escapeHtml(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
function inlineMd(t) {
  return t.replace(/`([^`]+)`/g, '<code>$1</code>').replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
}
function renderMarkdown(src) {
  const lines = escapeHtml(src).split('\n');
  const out = [];
  let inList = false;
  const closeList = () => { if (inList) { out.push('</ul>'); inList = false; } };
  for (const raw of lines) {
    if (/^###\s+/.test(raw)) { closeList(); out.push('<h4>' + inlineMd(raw.replace(/^###\s+/, '')) + '</h4>'); }
    else if (/^##\s+/.test(raw)) { closeList(); out.push('<h3>' + inlineMd(raw.replace(/^##\s+/, '')) + '</h3>'); }
    else if (/^#\s+/.test(raw)) { closeList(); out.push('<h3>' + inlineMd(raw.replace(/^#\s+/, '')) + '</h3>'); }
    else if (/^&gt;\s+/.test(raw)) { closeList(); out.push('<blockquote>' + inlineMd(raw.replace(/^&gt;\s+/, '')) + '</blockquote>'); }
    else if (/^[-*]\s+/.test(raw)) {
      if (!inList) { out.push('<ul>'); inList = true; }
      out.push('<li>' + inlineMd(raw.replace(/^[-*]\s+/, '')) + '</li>');
    }
    else if (raw.trim() === '') { closeList(); }
    else { closeList(); out.push('<p>' + inlineMd(raw) + '</p>'); }
  }
  closeList();
  return out.join('');
}

async function init() {
  await loadAgents();
  await loadInitialPage();      // 首屏只取一页（?limit=30），取代全量 loadHistory
  await loadAgentStatus();
  setInterval(pollNew, 2000);   // 每2秒增量轮询（带 since_id / 空会议室降级 limit）
  setInterval(loadAgentStatus, 3000); // 每3秒刷新阿编在线状态（pull 即心跳）
  const list = document.getElementById('message-list');
  if (list) {
    list.addEventListener('scroll', onListScroll);
  }
}

async function loadAgentStatus() {
  try {
    const res = await fetch('/api/agents/status');
    const data = await res.json();
    const me = (data.agents || []).find(a => a.name === 'WorkBuddy');
    const dot = document.getElementById('agent-status');
    const hint = document.getElementById('reawaken-hint');
    if (!dot) return;
    if (!me || !me.last_seen) {
      dot.className = 'status-dot idle';
      dot.textContent = '阿编·待命';
      if (hint) hint.style.display = 'none';
      return;
    }
    const ageSec = (Date.now() - new Date(me.last_seen).getTime()) / 1000;
    let cls, label, showHint = false;
    if (me.status === 'offline') {
      cls = 'idle'; label = '阿编·已收工';
    } else {
      const aliveWindow = me.session ? 600 : 120;
      if (ageSec > aliveWindow) {
        if (me.session) {
          cls = 'lost'; label = '阿编·已掉线·需重唤'; showHint = true;
        } else {
          cls = 'idle'; label = '阿编·离线';
        }
      } else if (me.status === 'working') {
        cls = 'working'; label = '阿编·处理中';
      } else {
        cls = 'waiting'; label = '阿编·待命中';
      }
    }
    dot.className = 'status-dot ' + cls;
    dot.textContent = label;
    if (hint) hint.style.display = showHint ? 'block' : 'none';
  } catch (e) { /* 状态接口异常不阻断聊天 */ }
}

async function loadAgents() {
  const res = await fetch('/api/agents');
  const data = await res.json();
  currentAgentList = data.agents;
  const select = document.getElementById('agent-select');
  select.innerHTML = '<option value="all">@所有人</option>';
  currentAgentList.forEach(name => {
    const opt = document.createElement('option');
    opt.value = name;
    opt.textContent = name;
    select.appendChild(opt);
  });
}

// 首屏：只取最新一页（?limit=30），顺序 appendMessage，初始化游标与去重集（AC-1.1）
async function loadInitialPage() {
  try {
    const res = await fetch('/api/messages/history?limit=30');
    const data = await res.json();
    const list = document.getElementById('message-list');
    list.innerHTML = '';        // 仅首屏一次性清空（非轮询/追加），其后不再整体重绘
    insertedIds.clear();
    clientNewestId = null;
    clientOldestId = null;
    pollCursorId = null;
    noMoreOlder = false;
    (data.messages || []).forEach(msg => appendMessage(msg));
    // appendMessage 已初始化 clientOldestId/clientNewestId；轮询游标同步到最新（BUG-A 修复）
    pollCursorId = clientNewestId;
    if (isAtBottom) scrollToBottom();
  } catch (e) { /* 首屏失败不阻断 */ }
}

// 2s 增量轮询：带 since_id 拉取轮询游标之后的新消息；空会议室降级为首屏语义（风险-E 修复）
async function pollNew() {
  try {
    let url = '/api/messages/history';
    if (pollCursorId !== null && pollCursorId !== undefined) {
      url += '?since_id=' + encodeURIComponent(pollCursorId);
    } else {
      url += '?limit=30';  // 首屏为空时降级拉取，避免永不刷新（风险-E）
    }
    const res = await fetch(url);
    const data = await res.json();
    const msgs = data.messages || [];
    if (msgs.length === 0) return;
    const atBottomBefore = isAtBottom;
    let newCount = 0;
    let lastServerId = null;
    msgs.forEach(msg => {
      lastServerId = msg.id;
      if (insertedIds.has(msg.id)) return; // 去重幂等
      appendMessage(msg);
      newCount++;
    });
    // 轮询游标只由服务端返回的消息推进，乐观发送不推进（BUG-C 修复：不丢更早的 agent 回复）
    if (lastServerId !== null) pollCursorId = lastServerId;
    // 非底部时弹浮动提示，N = 本次实际新增条数（AC-3.1）；底部不弹（AC-3.4）
    if (!atBottomBefore && newCount > 0) {
      showNewMessageBanner(newCount);
    }
  } catch (e) { /* 轮询失败不阻断 */ }
}

// 单条消息渲染节点（bubble + 可选 read-status），沿用原 renderMessages 的分支逻辑
function buildMessageNodes(msg) {
  const bubble = document.createElement('div');
  bubble.classList.add('message-bubble');
  if (msg.sender_type === 'user') {
    bubble.classList.add('user');
    bubble.textContent = msg.content;
  } else {
    bubble.classList.add('agent');
    bubble.innerHTML = '<div class="agent-name">' + escapeHtml(msg.sender_agent_name) + ':</div>' + renderMarkdown(msg.content);
  }
  const nodes = [bubble];
  if (msg.sender_type === 'user') {
    const status = document.createElement('div');
    status.classList.add('read-status');
    if (msg.target_type === 'single') {
      const isRead = msg.read_by && msg.read_by.includes(msg.target_agent_name);
      status.innerHTML = isRead ? '<span>✓</span> 已读' : '<span>○</span> 未读';
    } else if (msg.target_type === 'all') {
      const total = currentAgentList.length;
      const readCount = msg.read_by ? msg.read_by.length : 0;
      status.innerHTML = (readCount === total && total > 0) ? '✓✓ 全部已读' : `${readCount}/${total} 已读`;
    }
    nodes.push(status);
  }
  return nodes;
}

// 追加到列表底部；去重、初始化 oldest、更新 newest 游标；仅底部时滚底（AC-1.2/2.2/3.4）
function appendMessage(msg) {
  if (insertedIds.has(msg.id)) return;
  const list = document.getElementById('message-list');
  buildMessageNodes(msg).forEach(n => list.appendChild(n));
  insertedIds.add(msg.id);
  if (clientOldestId === null) clientOldestId = msg.id; // BUG-A 修复：首屏/任意路径都初始化 oldest
  clientNewestId = msg.id;
  if (isAtBottom) scrollToBottom();
}

// 前置插入到列表顶部；去重、更新 oldest 游标（AC-4.1）
function prependMessage(msg) {
  if (insertedIds.has(msg.id)) return;
  const list = document.getElementById('message-list');
  const frag = document.createDocumentFragment();
  buildMessageNodes(msg).forEach(n => frag.appendChild(n));
  list.insertBefore(frag, list.firstChild);
  insertedIds.add(msg.id);
  clientOldestId = msg.id;
}

// 触顶加载更早一页：before 游标前置插入并补偿 scrollTop 保持视口不跳动（AC-4.1/4.2）
async function loadOlder() {
  if (noMoreOlder || clientOldestId === null || loadingOlder) return;
  loadingOlder = true;
  try {
    const list = document.getElementById('message-list');
    const prevScrollTop = list.scrollTop;
    const prevScrollHeight = list.scrollHeight;
    const res = await fetch('/api/messages/history?before_id=' + encodeURIComponent(clientOldestId) + '&limit=30');
    const data = await res.json();
    const msgs = data.messages || [];
    if (msgs.length === 0) {
      noMoreOlder = true; // 已无更早历史，停止发起 before_id 请求（风险-B 修复）
    } else {
      // before 返回升序（最接近游标在前），reverse 后逐条 prepend 保持顺序正确
      msgs.slice().reverse().forEach(msg => prependMessage(msg));
    }
    // 补偿滚动位置：插入前 scrollTop + 插入内容高度，视口锚定不动（即使为空也不抖动）
    list.scrollTop = prevScrollTop + (list.scrollHeight - prevScrollHeight);
  } catch (e) { /* 失败不阻断 */ }
  finally {
    loadingOlder = false;
  }
}

function onListScroll() {
  const list = document.getElementById('message-list');
  isAtBottom = isNearBottom();
  // 触顶（距顶 ≤ 阈值）且未在加载更早、且仍有更早历史时，自动加载更早一页
  if (list.scrollTop < BOTTOM_THRESHOLD && !loadingOlder && !noMoreOlder) {
    loadOlder();
  }
}

function scrollToBottom() {
  const list = document.getElementById('message-list');
  list.scrollTop = list.scrollHeight - list.clientHeight;
}

function isNearBottom() {
  const list = document.getElementById('message-list');
  return list.scrollTop + list.clientHeight >= list.scrollHeight - BOTTOM_THRESHOLD;
}

// ---- 微信式浮动提示组件（AC-3.1 / 3.2 / 3.3 / 3.4）----
function ensureBanner() {
  let banner = document.getElementById('new-msg-banner');
  if (!banner) {
    banner = document.createElement('div');
    banner.id = 'new-msg-banner';
    banner.className = 'new-msg-banner';
    banner.style.display = 'none';
    banner.addEventListener('click', () => {
      scrollToBottom();
      hideBanner();
    });
    document.body.appendChild(banner);
  }
  return banner;
}

function showNewMessageBanner(n) {
  const banner = ensureBanner();
  banner.textContent = n + ' 条新消息';
  banner.style.display = 'block';
  if (bannerTimer) clearTimeout(bannerTimer);
  // 约 1000ms（落入 [900,1500]ms 容差）自动消失（AC-3.2）
  bannerTimer = setTimeout(hideBanner, 1000);
}

function hideBanner() {
  const banner = document.getElementById('new-msg-banner');
  if (banner) banner.style.display = 'none';
  bannerTimer = null;
}

async function sendMessage() {
  const input = document.getElementById('message-input');
  const content = input.value.trim();
  if (!content) return;
  const target = document.getElementById('agent-select').value;
  const payload = {
    sender_type: 'user',
    content: content,
    target_type: target === 'all' ? 'all' : 'single',
    target_agent_name: target === 'all' ? null : target
  };
  const res = await fetch('/api/messages/send', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  });
  // BUG-D 修复：发送失败（如 single 目标 agent 不存在）不追加、不污染游标，避免退化为全量回放
  if (!res.ok) {
    input.value = '';
    return;
  }
  const data = await res.json();
  input.value = '';
  if (!data || !data.message_id) {
    return;
  }
  // 乐观追加：用返回的 message_id + 输入框 content 即时可见（AC-5.1/5.2），不走全量 loadHistory
  // 注意：此处只推进展示游标 clientNewestId，不推进轮询游标 pollCursorId（BUG-C 修复）
  const optimisticMsg = {
    id: data.message_id,
    content: content,
    sender_type: 'user',
    sender_agent_name: null,
    target_type: payload.target_type,
    target_agent_name: payload.target_agent_name,
    created_at: '',
    client_msg_id: null,
    read_by: []
  };
  appendMessage(optimisticMsg);
}

document.getElementById('send-btn').addEventListener('click', sendMessage);
document.getElementById('message-input').addEventListener('keypress', (e) => {
  if (e.key === 'Enter') sendMessage();
});

init();
