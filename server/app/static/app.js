let currentAgentList = [];

// ---- 增量加载 / 浮动提示 模块级状态（T-meeting-incremental）----
let clientNewestId = null;     // 当前已渲染的最新消息 id（since 游标基准）
let clientOldestId = null;     // 当前已渲染的最旧消息 id（before 游标基准）
let insertedIds = new Set();   // 已插入消息 id 集合，去重幂等（AC-2.3）
let loadingOlder = false;      // 触顶加载守卫，防并发重入
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
  setInterval(pollNew, 2000);   // 每2秒增量轮询（带 since_id）
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
      // 通过「结束会议」正常收工
      cls = 'idle'; label = '阿编·已收工';
    } else {
      // 会话中：pull 间隙窗口放宽到 600s；非会话：120s
      const aliveWindow = me.session ? 600 : 120;
      if (ageSec > aliveWindow) {
        if (me.session) {
          // 会话仍 active 但大脑循环意外中断 → 需重唤（老板要的"为什么离线"）
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
    (data.messages || []).forEach(msg => appendMessage(msg));
    if (isAtBottom) scrollToBottom();
  } catch (e) { /* 首屏失败不阻断 */ }
}

// 2s 增量轮询：只拉 since_id 之后的新消息，顺序 appendMessage（AC-2.1）
async function pollNew() {
  if (clientNewestId === null) return;  // 首屏未完成不轮询
  try {
    const res = await fetch('/api/messages/history?since_id=' + encodeURIComponent(clientNewestId));
    const data = await res.json();
    const msgs = data.messages || [];
    if (msgs.length === 0) return;
    const atBottomBefore = isAtBottom;
    let newCount = 0;
    msgs.forEach(msg => {
      if (insertedIds.has(msg.id)) return; // 去重幂等
      appendMessage(msg);
      newCount++;
    });
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

// 追加到列表底部；去重、更新 newest 游标；仅底部时滚底（AC-1.2/2.2/3.4）
function appendMessage(msg) {
  if (insertedIds.has(msg.id)) return;
  const list = document.getElementById('message-list');
  buildMessageNodes(msg).forEach(n => list.appendChild(n));
  insertedIds.add(msg.id);
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
  if (clientOldestId === null || loadingOlder) return;
  loadingOlder = true;
  try {
    const list = document.getElementById('message-list');
    const prevScrollTop = list.scrollTop;
    const prevScrollHeight = list.scrollHeight;
    const res = await fetch('/api/messages/history?before_id=' + encodeURIComponent(clientOldestId) + '&limit=30');
    const data = await res.json();
    const msgs = data.messages || [];
    // before 返回升序（最接近游标在前），reverse 后逐条 prepend 保持顺序正确
    msgs.slice().reverse().forEach(msg => prependMessage(msg));
    // 补偿滚动位置：插入前 scrollTop + 插入内容高度，视口锚定不动
    list.scrollTop = prevScrollTop + (list.scrollHeight - prevScrollHeight);
  } catch (e) { /* 失败不阻断 */ }
  finally {
    loadingOlder = false;
  }
}

function onListScroll() {
  const list = document.getElementById('message-list');
  isAtBottom = isNearBottom();
  // 触顶（距顶 ≤ 阈值）且未在加载更早时，自动加载更早一页
  if (list.scrollTop < BOTTOM_THRESHOLD && !loadingOlder) {
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
  const data = await res.json();
  input.value = '';
  // 乐观追加：用返回的 message_id + 输入框 content 即时可见（AC-5.1/5.2），不走全量 loadHistory
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
