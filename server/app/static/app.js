let currentAgentList = [];

// F-g.2 / Q5：前端离线着色窗口配置（来自 GET /api/config，避免硬编码 1200/600）。
// 兜底：读取失败则用 offline_window=7200 / online_window=1200（design §5.4）。
let cfg = { offline_window: 7200, online_window: 1200 };

// ---- 相对路径 base（B 修复）：兼容反代剥离前缀 ----
// '/meeting' → '/meeting/'；'/meeting/' → '/meeting/'；'/' → '/'
// 全部 API 都基于该 base 拼接，域名反代形态（agnes.owen1.de5.net/meeting）下不再 404。
var API_BASE = (function () {
  var p = location.pathname;
  return p.charAt(p.length - 1) === '/' ? p : p + '/';
})();

// ---- C（keyboard-v15）：移动端判定（触屏/多点触控/UA 三取最稳组合）----
// 只有手机/触屏设备触发「聚焦浮动到顶部」；PC 上聚焦不得浮动。
var IS_MOBILE = ('ontouchstart' in window) || (navigator.maxTouchPoints > 0) || /Android|iPhone|iPad|Mobile/i.test(navigator.userAgent);

// ---- 增量加载 / 浮动提示 模块级状态（T-meeting-incremental）----
let clientNewestId = null;     // 当前已渲染的最新消息 id（展示用）
let clientOldestId = null;     // 当前已渲染的最旧消息 id（before 游标基准）
let pollCursorId = null;       // 轮询游标：只由「服务端返回的消息」推进，乐观发送不推进（BUG-C 修复）
let insertedIds = new Set();   // 已插入消息 id 集合，去重幂等（AC-2.3）
let pendingMap = new Map();    // 乐观消息状态：tempId -> {content,targetType,targetAgentName,clientMsgId,sendStatus:'sending'|'sent'|'failed'}（AC-2.3）
let tempToServerId = new Map(); // tempId -> serverId（乐观消息升级记录，供幂等/调试）
let readStatusNodes = new Map(); // 消息 id -> 已读徽标 DOM 节点（仅 user 消息），用于增量刷新时原地重画 read 状态
let loadingOlder = false;      // 触顶加载守卫，防并发重入
let noMoreOlder = false;       // 已到最早历史，停止发起 before_id 请求（风险-B 修复）
let isAtBottom = true;         // 用户是否位于列表最底层
const BOTTOM_THRESHOLD = 4;    // 距底/距顶 ≤4px 视为「在底部」/「触顶」
let bannerTimer = null;        // 浮动提示自动消失计时器句柄

// F3：生成 client_msg_id（UUID v4，前缀 usr_），供后端幂等去重（防网络重试重复保存）。
function genClientMsgId() {
  let uuid;
  if (typeof crypto !== 'undefined' && crypto.randomUUID) {
    uuid = crypto.randomUUID();
  } else {
    uuid = 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
      const r = (Math.random() * 16) | 0;
      const v = c === 'x' ? r : (r & 0x3) | 0x8;
      return v.toString(16);
    });
  }
  return 'usr_' + uuid;
}

// 乐观渲染本地临时 id（temp_<uuid>）：服务端 message_id 尚未返回前用于占位，成功后升级替换（AC-1.1/1.2）。
function genTempId() {
  let uuid;
  if (typeof crypto !== 'undefined' && crypto.randomUUID) {
    uuid = crypto.randomUUID();
  } else {
    uuid = 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
      const r = (Math.random() * 16) | 0;
      const v = c === 'x' ? r : (r & 0x3) | 0x8;
      return v.toString(16);
    });
  }
  return 'temp_' + uuid;
}

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
  await loadConfig();           // F-g.2 / Q5：先读配置（offline_window 等）
  await loadAgents();
  await loadInitialPage();      // 首屏只取一页（?limit=30），取代全量 loadHistory
  await loadAgentStatus();
  setupPanel();                 // F-e / §3.5：☰ 面板（事件绑定 + 列表渲染）
  setInterval(loadAgentStatus, 3000); // presence：状态栏 ≤5s 刷新（拍板 + PRD §2.5）
  setInterval(pollNew, 2000);   // 每2秒增量轮询（带 since_id / 空会议室降级 limit）
  setInterval(refreshReadReceipts, 5000); // 每5秒同步已读回执，刷新已渲染消息的 ✓/○ 徽标
  setInterval(loadAgents, 30000); // F6：每30秒刷新 agent 下拉，新注册 agent 自动出现、保留当前选中值
  const list = document.getElementById('message-list');
  if (list) {
    list.addEventListener('scroll', onListScroll);
  }
  setupKeyboardHandling();  // EXT-2：移动端输入法遮挡处理
  const mi = document.getElementById('message-input');
  if (mi) {
    mi.addEventListener('input', autoGrowInput);  // EXT-3：输入时自动增高
    autoGrowInput();
  }
}

// F-g.2 / Q5：从 GET /api/config 读取离线着色窗口等配置（替换硬编码 1200/600）。
async function loadConfig() {
  try {
    const res = await fetch(API_BASE + 'api/config');
    const data = await res.json();
    if (data && data.offline_window) cfg.offline_window = data.offline_window;
    if (data && data.online_window) cfg.online_window = data.online_window;
  } catch (e) {
    // 兜底：保留 offline_window=7200 / online_window=1200
  }
}

// presence（服务端权威三态 online/lost/offline）：统一在线判定。
// 优先用 /api/agents/status 返回的 presence 字段；fallback 按 session 取 offline/online 窗口（来自 cfg）。
function isOnline(a) {
  if (a.presence !== undefined) return a.presence === 'online';
  if (!a.last_seen) return false;
  // F-g.2 / Q5：窗口读 cfg（session=开会态用 offline_window，否则 online_window），消除硬编码 1200
  const win = a.session ? cfg.offline_window : cfg.online_window;
  return a.status !== 'offline' &&
         (Date.now() - new Date(a.last_seen).getTime()) / 1000 <= win;
}

async function loadAgentStatus() {
  const container = document.getElementById('agent-status');
  if (!container) return;   // 容器不存在时直接跳过，不发无效请求
  try {
    const res = await fetch(API_BASE + 'api/agents/status');
    const data = await res.json();
    // 状态栏只显示在线 agent（presence 服务端权威判定）；offline/lost 一律不渲染（老板拍板 §5.1-3）。
    container.innerHTML = '';
    (data.agents || []).forEach(a => {
      if (!isOnline(a)) return;   // 离线/失联不显示
      const name = a.name || '';
      const dot = document.createElement('span');
      dot.className = 'status-dot';
      if (a.thinking) {
        dot.classList.add('thinking');
        dot.textContent = name + '·思考中';
      } else if (a.status === 'working') {
        dot.classList.add('working');
        dot.textContent = name + '·处理中';
      } else if (a.has_unread) {
        dot.classList.add('waiting');
        dot.textContent = name + '·处理任务';
      } else {
        dot.classList.add('waiting');
        dot.textContent = name + '·待命中';
      }
      container.appendChild(dot);
    });
  } catch (e) { /* 状态接口异常不阻断聊天 */ }
}

async function loadAgents() {
  try {
    const res = await fetch(API_BASE + 'api/agents');
    const data = await res.json();
    currentAgentList = data.agents;
    const select = document.getElementById('agent-select');
    if (!select) return;   // no picker UI, but keep currentAgentList fresh
    const prev = select.value;  // F6：刷新前记录当前选中值
    select.innerHTML = '<option value="all">@所有人</option>';
    currentAgentList.forEach(name => {
      const opt = document.createElement('option');
      opt.value = name;
      opt.textContent = name;
      select.appendChild(opt);
    });
    // F6：重建 options 后还原选中值（若新列表仍包含原选项），既有 Agent 不丢、新注册自动出现
    if (prev && Array.from(select.options).some(o => o.value === prev)) {
      select.value = prev;
    }
    // 注：上下线提示由持久化系统消息（sender_type=system）承担（Q2=A），
    // 前端不再做 diff 临时 DOM 提示（app.js 旧版「X 加入了/离开了群组」已移除）。
  } catch (e) {
    // 旧的缓存 JS 可能读到新 DOM（无 #agent-select）或 /api/agents 异常。
    // 下拉框失败不阻断首屏消息列表渲染，避免产生白屏。
  }
}

// 首屏：只取最新一页（?limit=30），顺序 appendMessage，初始化游标与去重集（AC-1.1）
async function loadInitialPage() {
  try {
    const res = await fetch(API_BASE + 'api/messages/history?limit=30');
    const data = await res.json();
    const list = document.getElementById('message-list');
    list.innerHTML = '';        // 仅首屏一次性清空（非轮询/追加），其后不再整体重绘
    insertedIds.clear();
    readStatusNodes.clear();
    pendingMap.clear();         // 页面初始化：乐观发送状态重置（新页面无在途消息）
    tempToServerId.clear();
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
    let url = API_BASE + 'api/messages/history';
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
      // 系统消息（presence_event）：正常 appendMessage（参与 insertedIds 去重、参与 lastServerId 推进，
      // since_id 语义下推进到响应最后一个 id 安全），但不计入 newCount（AC-4.2 不弹「N 条新消息」）。
      if (msg.sender_type === 'system') {
        appendMessage(msg);
        return;
      }
      const wasInserted = insertedIds.has(msg.id);
      // appendMessage 内部：已存在则原地刷新已读徽标（read_by 可能已变），不存在则新建
      appendMessage(msg);
      if (!wasInserted) newCount++;
    });
    // 轮询游标只由服务端返回的消息推进，乐观发送不推进（BUG-C 修复：不丢更早的 agent 回复）
    if (lastServerId !== null) pollCursorId = lastServerId;
    // 非底部时弹浮动提示，N = 本次实际新增条数（AC-3.1）；底部不弹（AC-3.4）
    if (!atBottomBefore && newCount > 0) {
      showNewMessageBanner(newCount);
    }
  } catch (e) { /* 轮询失败不阻断 */ }
}

// 根据消息的 read_by 重新计算并写入「已读徽标」文本（single: ✓已读/○未读；all: N/N 已读/✓✓ 全部已读）
function paintReadStatus(msg, statusEl) {
  if (msg.target_type === 'single') {
    const isRead = msg.read_by && msg.read_by.includes(msg.target_agent_name);
    statusEl.innerHTML = isRead ? '<span>✓</span> 已读' : '<span>○</span> 未读';
  } else if (msg.target_type === 'all') {
    const total = currentAgentList.length;
    const readCount = msg.read_by ? msg.read_by.length : 0;
    statusEl.innerHTML = (readCount === total && total > 0) ? '✓✓ 全部已读' : `${readCount}/${total} 已读`;
  }
}

// 单条消息渲染节点：头像 + 内容列（名字 + 气泡）行布局（Telegram 风换皮）。
// 返回 [row, status?]，与旧 [bubble, status?] 结构一致，appendMessage/prependMessage 无需改。
function buildMessageNodes(msg) {
  // 系统消息（presence_event / doc_event）：灰色居中提示（.sys-notice）。
  // AC-4.1/修 M6：doc_event 走 renderSystemContent 渲染 [text](url) 链接；
  // presence_event（init/end/lost 等）直接 textContent，无链接不触发。
  if (msg.sender_type === 'system') {
    const notice = document.createElement('div');
    notice.className = 'sys-notice';
    notice.innerHTML = renderSystemContent(msg);
    notice.dataset.id = msg.id;
    return [notice];
  }
  const isUser = msg.sender_type === 'user';
  const row = document.createElement('div');
  row.className = 'msg-row ' + (isUser ? 'msg-out' : 'msg-in');
  row.dataset.id = msg.id;   // 供乐观升级/删除定位 DOM（AC-1.2/2.3）

  // 头像：agent 内联 bot SVG（品牌蓝底），user 文字"我"（灰底）
  const avatar = document.createElement('div');
  avatar.className = 'msg-avatar';
  if (isUser) {
    avatar.textContent = '我';
  } else {
    avatar.innerHTML = '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 8V4H8"/><rect width="16" height="12" x="4" y="8" rx="2"/><path d="M2 14h2"/><path d="M20 14h2"/><path d="M15 13v2"/><path d="M9 13v2"/></svg>';
  }

  // 内容列：名字 + 气泡
  const content = document.createElement('div');
  content.className = 'msg-content';

  const bubble = document.createElement('div');
  bubble.classList.add('msg-bubble');
  if (isUser) {
    bubble.classList.add('user');
    bubble.textContent = msg.content;
  } else {
    bubble.classList.add('agent');
    // 发送者名字置于气泡上方（.msg-content 首个子节点），与气泡保留 3px 间距
    const nameEl = document.createElement('div');
    nameEl.className = 'agent-name';
    nameEl.textContent = msg.sender_agent_name + ':';
    content.appendChild(nameEl);
    bubble.innerHTML = renderMarkdown(msg.content);
  }
  content.appendChild(bubble);
  row.appendChild(avatar);
  row.appendChild(content);

  const nodes = [row];
  if (isUser) {
    const status = document.createElement('div');
    status.classList.add('read-status');
    const pend = pendingMap.get(msg.id);
    if (pend) {
      // 乐观消息（未落盘确认）：渲染 发送中/失败态（失败含重试/删除按钮），不画已读徽标（AC-1.1/2.3）
      renderPendingStatus(status, msg.id, pend);
    } else {
      paintReadStatus(msg, status);
    }
    status._readSig = (msg.read_by || []).join(','); // 记录上次 read_by 签名，供增量刷新时跳过无变化项
    readStatusNodes.set(msg.id, status);             // 登记节点，供后续原地刷新 read 状态
    nodes.push(status);
  }
  return nodes;
}

// 渲染乐观消息的状态区：sending → 「发送中…」；failed → 「⚠ 发送失败」+ 重试/删除按钮（AC-2.3）。
function renderPendingStatus(statusEl, tempId, pend) {
  statusEl.classList.remove('msg-failed');
  statusEl.innerHTML = '';
  if (pend.sendStatus === 'failed') {
    statusEl.classList.add('msg-failed');
    const tip = document.createElement('span');
    tip.className = 'msg-failed-tip';
    tip.textContent = '⚠ 发送失败';
    const retryBtn = document.createElement('button');
    retryBtn.className = 'msg-failed-btn';
    retryBtn.textContent = '重试';
    retryBtn.addEventListener('click', function () { retryMessage(tempId); });
    const delBtn = document.createElement('button');
    delBtn.className = 'msg-failed-btn';
    delBtn.textContent = '删除';
    delBtn.addEventListener('click', function () { removeMessage(tempId); });
    statusEl.appendChild(tip);
    statusEl.appendChild(retryBtn);
    statusEl.appendChild(delBtn);
  } else {
    statusEl.textContent = '发送中…';
  }
}

// 原地重画某条乐观消息的状态区（发送中 ↔ 失败态切换）。
function repaintPendingStatus(tempId) {
  const st = readStatusNodes.get(tempId);
  const pend = pendingMap.get(tempId);
  if (!st || !pend) return;
  renderPendingStatus(st, tempId, pend);
}

// 按 data-id 定位消息行 DOM（id 为 temp_<uuid> / msg_<hex>，仅含安全字符）。
function findRowById(id) {
  const list = document.getElementById('message-list');
  if (!list) return null;
  return list.querySelector('[data-id="' + id + '"]');
}

// 追加到列表底部；去重时原地刷新已读徽标（read_by 可能已变），不重建气泡、不 innerHTML 清空
function appendMessage(msg) {
  if (msg.visible === 0) return;   // F-d / AC-4.3：不渲染可见性为 0 的系统确认消息（双保险，history 已过滤）
  const list = document.getElementById('message-list');
  if (insertedIds.has(msg.id)) {
    // 已渲染：只根据最新 read_by 重画已读徽标（修复「已读状态不随数据同步刷新」）
    const st = readStatusNodes.get(msg.id);
    if (st) {
      st._readSig = (msg.read_by || []).join(',');
      paintReadStatus(msg, st);
    }
    return;
  }
  buildMessageNodes(msg).forEach(n => list.appendChild(n));
  insertedIds.add(msg.id);
  if (clientOldestId === null) clientOldestId = msg.id; // BUG-A 修复：首屏/任意路径都初始化 oldest
  clientNewestId = msg.id;
  if (isAtBottom) scrollToBottom();
}

// 前置插入到列表顶部；去重时原地刷新已读徽标；更新 oldest 游标（AC-4.1）
function prependMessage(msg) {
  if (msg.visible === 0) return;   // F-d / AC-4.3：不渲染可见性为 0 的系统确认消息
  const list = document.getElementById('message-list');
  if (insertedIds.has(msg.id)) {
    const st = readStatusNodes.get(msg.id);
    if (st) {
      st._readSig = (msg.read_by || []).join(',');
      paintReadStatus(msg, st);
    }
    return;
  }
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
    const res = await fetch(API_BASE + 'api/messages/history?before_id=' + encodeURIComponent(clientOldestId) + '&limit=30');
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

// 周期性已读回执同步：拉取最新 read_by 并原地刷新已渲染消息的徽标。
// 仅更新已登记节点、跳过无变化项，绝不重建气泡或 innerHTML 清空，保留增量加载（AC-1.2）。
async function refreshReadReceipts() {
  // F10：无已渲染 user 消息（空聊天）时跳过轮询请求，避免无谓的 history 拉取
  if (readStatusNodes.size === 0) return;
  try {
    const res = await fetch(API_BASE + 'api/messages/history?limit=200');  // F5：有界拉取（仅用于刷新已渲染消息的 ✓/○ 徽标）
    const data = await res.json();
    (data.messages || []).forEach(msg => {
      if (!insertedIds.has(msg.id)) return;        // 只处理已渲染的消息
      const st = readStatusNodes.get(msg.id);
      if (!st) return;                              // 仅 user 消息有已读徽标
      const sig = (msg.read_by || []).join(',');
      if (st._readSig === sig) return;              // 无变化则跳过，避免无效 DOM 写
      st._readSig = sig;
      paintReadStatus(msg, st);
    });
  } catch (e) { /* 失败不阻断 */ }
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

// ---- EXT-3：输入框自适应高度（textarea）----
// 输入时按 scrollHeight 增高，封顶 MAX_INPUT_H 后内部滚动；发送后由 sendMessage 调回重置。
const MAX_INPUT_H = 120;
function autoGrowInput() {
  const el = document.getElementById('message-input');
  if (!el) return;
  el.style.height = 'auto';
  const h = Math.min(el.scrollHeight, MAX_INPUT_H);
  el.style.height = h + 'px';
  el.style.overflowY = el.scrollHeight > MAX_INPUT_H ? 'auto' : 'hidden';
}

// ---- EXT-2（IME 修复）：聚焦把消息列表滚到底，让最新消息与输入框可见 ----
// 消息列表是内部滚动容器 #message-list（非整页滚动）；视口随键盘收缩时
// .input-area 因 sticky 底 + flex 列始终可见，无需再手动加 padding。
function setupKeyboardHandling() {
  const input = document.getElementById('message-input');
  const list = document.getElementById('message-list');
  const inputArea = document.querySelector('.input-area');
  if (!input || !list) return;
  input.addEventListener('focus', function () {
    // C（keyboard-v15）：仅移动端把输入栏浮动到视口顶部（键盘从底部弹起完全碰不到）；PC 不浮动
    if (IS_MOBILE && inputArea) inputArea.classList.add('ime-top');
    setTimeout(function () {
      list.scrollTop = list.scrollHeight;                       // 最新消息滚入视口
      window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' }); // 与参考 HTML 对齐（整页滚动布局下生效，本布局为 no-op 但不影响）
    }, 350);
  });
  input.addEventListener('blur', function () {
    if (IS_MOBILE && inputArea) inputArea.classList.remove('ime-top');  // 失焦回到底部
    setTimeout(function () {
      if (window.innerHeight < window.outerHeight - 50) {       // 键盘曾弹起
        window.scrollTo({ top: 0, behavior: 'smooth' });
      }
    }, 100);
  });
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

// 乐观发送（AC-1.1/1.2/2.3/2.4）：点击发送 → 立即渲染临时气泡（不等服务端响应）→ 并行 POST。
// 成功 → upgradeOptimisticMsg 升级替换（tempId → serverId，insertedIds 幂等不重复）；失败 → 保留显示 + 重试/删除。
// 游标纪律：乐观渲染只推进展示游标 clientNewestId，绝不推进轮询游标 pollCursorId（BUG-C）；
//          失败时恢复 clientNewestId（BUG-D：失败不更新 clientNewestId，展示游标停在最后一条已确认消息上）。
async function sendMessage() {
  const input = document.getElementById('message-input');
  const content = input.value.trim();
  if (!content) return;
  const sel = document.getElementById('agent-select');
  const target = sel ? sel.value : 'all';
  const targetType = target === 'all' ? 'all' : 'single';
  const targetAgentName = target === 'all' ? null : target;
  const tempId = genTempId();
  const clientMsgId = genClientMsgId();   // 每次发送生成新 client_msg_id（AC-2.4：同内容两次发送互不覆盖）
  const prevNewestId = clientNewestId;    // 失败时恢复展示游标用（BUG-D）

  // 1) 立即渲染乐观气泡（AC-1.1：≤500ms 出现、不等待 POST 响应）
  const optimisticMsg = {
    id: tempId,
    content: content,
    sender_type: 'user',
    sender_agent_name: null,
    target_type: targetType,
    target_agent_name: targetAgentName,
    created_at: '',
    client_msg_id: clientMsgId,
    read_by: []
  };
  pendingMap.set(tempId, {
    content: content,
    targetType: targetType,
    targetAgentName: targetAgentName,
    clientMsgId: clientMsgId,
    sendStatus: 'sending'
  });
  appendMessage(optimisticMsg);   // insertedIds.add(tempId) 由 appendMessage 完成
  input.value = '';               // 输入框立即清空（AC-1.1）
  autoGrowInput();                // EXT-3：发送后复位输入框高度到 1 行

  // 2) 并行发请求（服务端保持同步落盘；UI 不等落盘完成，AC-1.1/AC-2.1）
  let res;
  try {
    res = await fetch(API_BASE + 'api/messages/send', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        sender_type: 'user',
        content: content,
        target_type: targetType,
        target_agent_name: targetAgentName,
        client_msg_id: clientMsgId
      })
    });
  } catch (e) {
    markMessageFailed(tempId, prevNewestId);
    return;
  }
  if (!res.ok) {
    // BUG-D：发送失败（400 目标不存在 / 5xx）→ 保留显示 + 标记「发送失败」，不污染游标
    markMessageFailed(tempId, prevNewestId);
    return;
  }
  const data = await res.json().catch(() => null);
  if (!data || !data.message_id) {
    markMessageFailed(tempId, prevNewestId);
    return;
  }
  upgradeOptimisticMsg(tempId, data.message_id);
}

// 乐观消息升级替换（AC-1.2 幂等关键）：tempId → serverId。
// 轮询/历史随后带回 serverId 时 insertedIds.has(serverId) → 不重复追加。
function upgradeOptimisticMsg(tempId, serverId) {
  const row = findRowById(tempId);
  if (row) row.dataset.id = serverId;   // DOM data-id 升级
  insertedIds.delete(tempId);
  insertedIds.add(serverId);
  const pend = pendingMap.get(tempId);
  if (pend) pend.sendStatus = 'sent';
  tempToServerId.set(tempId, serverId);
  // 已读徽标节点登记：id 从 temp 换成 server
  const st = readStatusNodes.get(tempId);
  if (st) {
    readStatusNodes.delete(tempId);
    readStatusNodes.set(serverId, st);
    // 升级后重新画真实已读徽标（服务端数据）
    st.classList.remove('msg-failed');
    st.innerHTML = '';
    paintReadStatus({ id: serverId, target_type: pend ? pend.targetType : 'all', target_agent_name: pend ? pend.targetAgentName : null, read_by: [] }, st);
  }
  // 展示游标：若最新位是 tempId，升级为 serverId（已确认消息占据最新位）
  if (clientNewestId === tempId) clientNewestId = serverId;
  pendingMap.delete(tempId);   // 发送完成（保留 tempToServerId 供追溯）
}

// 发送失败兜底（AC-2.3）：保留气泡 + 标记「发送失败」+ 重试/删除按钮；恢复展示游标（BUG-D）。
function markMessageFailed(tempId, prevNewestId) {
  const pend = pendingMap.get(tempId);
  if (!pend) return;
  pend.sendStatus = 'failed';
  repaintPendingStatus(tempId);
  // BUG-D：失败消息不更新 clientNewestId（展示游标停在最后一条已确认消息上）
  if (prevNewestId !== undefined) clientNewestId = prevNewestId;
}

// 重试（AC-2.3）：复用同一 client_msg_id → 若上次实际已落盘仅响应丢失，服务端幂等返回 dup 同样给 message_id。
async function retryMessage(tempId) {
  const pend = pendingMap.get(tempId);
  if (!pend || pend.sendStatus !== 'failed') return;
  pend.sendStatus = 'sending';
  repaintPendingStatus(tempId);
  let res;
  try {
    res = await fetch(API_BASE + 'api/messages/send', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        sender_type: 'user',
        content: pend.content,
        target_type: pend.targetType,
        target_agent_name: pend.targetAgentName,
        client_msg_id: pend.clientMsgId   // 复用同一 client_msg_id（幂等）
      })
    });
  } catch (e) {
    pend.sendStatus = 'failed';
    repaintPendingStatus(tempId);
    return;
  }
  if (!res.ok) {
    pend.sendStatus = 'failed';
    repaintPendingStatus(tempId);
    return;
  }
  const data = await res.json().catch(() => null);
  if (!data || !data.message_id) {
    pend.sendStatus = 'failed';
    repaintPendingStatus(tempId);
    return;
  }
  upgradeOptimisticMsg(tempId, data.message_id);
}

// 删除（AC-2.3）：仅对本地乐观消息（未落盘）生效——DOM 移除 + 状态清理；已落盘消息不提供删除。
function removeMessage(tempId) {
  const pend = pendingMap.get(tempId);
  if (!pend) return;
  const row = findRowById(tempId);
  if (row) row.remove();
  const st = readStatusNodes.get(tempId);
  if (st) st.remove();
  readStatusNodes.delete(tempId);
  insertedIds.delete(tempId);
  pendingMap.delete(tempId);
  tempToServerId.delete(tempId);
  // clientNewestId 已在失败时恢复（BUG-D），此处无需再动
}

// ---- F-e / §3.5：☰ 浮动管理面板（不跳页）----
// 四态色标（presence / status / has_unread / last_seen==registered_at 推导，design §5.2）：
//   待接入(蓝) / 待命(黄) / 处理中(绿) / 已收工(灰)
function derivePanelState(a) {
  if (a.presence === 'offline' || a.status === 'offline') {
    return { key: 'st-offline', label: '已收工' };          // 灰
  }
  if (a.last_seen && a.last_seen === a.registered_at) {
    return { key: 'st-pending', label: '待接入' };           // 蓝（注册后从未 pull）
  }
  if (a.status === 'working' || a.has_unread) {
    return { key: 'st-active', label: '处理中' };            // 绿
  }
  return { key: 'st-idle', label: '待命' };                   // 黄
}

function setupPanel() {
  const toggle = document.getElementById('panel-toggle');
  const close = document.getElementById('panel-close');
  const panel = document.getElementById('agent-panel');
  if (toggle && panel) {
    toggle.addEventListener('click', async () => {
      panel.classList.toggle('hidden');                      // 仅切换 display，URL 不变、不跳页（AC-5.1）
      if (!panel.classList.contains('hidden')) {
        await loadPanelAgents();                             // 展开即拉最新列表
      }
    });
  }
  if (close && panel) {
    close.addEventListener('click', () => panel.classList.add('hidden'));
  }
  const form = document.getElementById('panel-create-form');
  if (form) {
    form.addEventListener('submit', onCreateAgent);
  }
}

async function loadPanelAgents() {
  const list = document.getElementById('panel-agent-list');
  if (!list) return;
  try {
    const res = await fetch(API_BASE + 'api/agents/manage/list');
    const data = await res.json();
    list.innerHTML = '';
    (data.agents || []).forEach(a => list.appendChild(buildPanelRow(a)));
  } catch (e) { /* 面板加载失败不阻断聊天 */ }
}

function buildPanelRow(a) {
  const row = document.createElement('div');
  row.className = 'panel-agent-row';
  row.dataset.name = a.name || '';

  const nameEl = document.createElement('div');
  nameEl.className = 'pa-name';
  nameEl.textContent = a.name || '';

  const descEl = document.createElement('div');                 // F-h / AC-5.2/8.2：角色介绍
  descEl.className = 'pa-desc';
  descEl.textContent = a.description || '';

  const st = derivePanelState(a);
  const stateEl = document.createElement('span');               // §3.5 四态色标
  stateEl.className = 'pa-state ' + st.key;
  stateEl.textContent = st.label;

  const lastSeen = document.createElement('div');               // AC-5.2：最后活动
  lastSeen.className = 'pa-last';
  lastSeen.textContent = '最后活动: ' + (a.last_seen || '-');

  const scopeSel = document.createElement('select');            // F-e.2 / AC-5.4：可见性下拉
  scopeSel.className = 'pa-scope';
  scopeSel.innerHTML = '<option value="all">所有人</option><option value="direct">仅私信</option>';
  scopeSel.value = a.read_scope || 'all';
  scopeSel.addEventListener('change', () => onUpdateAgent(a.name, null, scopeSel.value));

  const descInput = document.createElement('input');            // F-e.2 / AC-5.4：行内改角色介绍
  descInput.className = 'pa-desc-input';
  descInput.type = 'text';
  descInput.placeholder = '改角色介绍';
  descInput.value = a.description || '';

  const saveDesc = document.createElement('button');
  saveDesc.className = 'pa-btn';
  saveDesc.textContent = '保存';
  saveDesc.addEventListener('click', () => onUpdateAgent(a.name, descInput.value, scopeSel.value));

  const delBtn = document.createElement('button');             // AC-5.2/5.5：删除按钮
  delBtn.className = 'pa-btn pa-del';
  delBtn.textContent = '删除';
  delBtn.addEventListener('click', () => onDeleteAgent(a.name));

  const meta = document.createElement('div');
  meta.className = 'pa-meta';
  meta.appendChild(stateEl);
  meta.appendChild(lastSeen);

  const actions = document.createElement('div');
  actions.className = 'pa-actions';
  actions.appendChild(descInput);
  actions.appendChild(saveDesc);
  actions.appendChild(scopeSel);
  actions.appendChild(delBtn);

  row.appendChild(nameEl);
  row.appendChild(descEl);
  row.appendChild(meta);
  row.appendChild(actions);
  return row;
}

// F-e.3 / AC-5.3：创建表单提交 → manage/create → 列表立即刷新
async function onCreateAgent(e) {
  e.preventDefault();
  const name = document.getElementById('panel-name').value.trim();
  const desc = document.getElementById('panel-desc').value.trim();
  const scope = document.getElementById('panel-scope').value;
  if (!name) return;
  try {
    await fetch(API_BASE + 'api/agents/manage/create', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: name, description: desc, read_scope: scope }),
    });
    document.getElementById('panel-name').value = '';
    document.getElementById('panel-desc').value = '';
    await loadPanelAgents();   // 实时刷新（AC-5.3）
    loadAgents();              // 同步刷新聊天下拉
  } catch (e) { /* 失败不阻断 */ }
}

// F-i / AC-5.4/9.2：行内改 description/read_scope → manage/update → 列表实时刷新
async function onUpdateAgent(name, description, read_scope) {
  const body = { name: name };
  if (description !== null && description !== undefined) body.description = description;
  if (read_scope) body.read_scope = read_scope;
  try {
    await fetch(API_BASE + 'api/agents/manage/update', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    await loadPanelAgents();   // 实时刷新（AC-5.4/9.2）
  } catch (e) { /* 失败不阻断 */ }
}

// F-f / AC-5.5：删除 → manage/delete → 列表移除
async function onDeleteAgent(name) {
  try {
    await fetch(API_BASE + 'api/agents/manage/delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: name }),
    });
    await loadPanelAgents();   // 实时刷新（AC-5.5）
    loadAgents();              // 同步刷新聊天下拉
  } catch (e) { /* 失败不阻断 */ }
}

// ---- 入口：等待 DOM 就绪 ----
// 脚本经 index.html <head> 内 document.write 动态写入，会在 body 解析前执行，
// 必须等 DOMContentLoaded 后再绑定控件与启动 init()（body 末尾加载时 readyState 已非 loading，立即执行）。
function runWhenReady(fn) {
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', fn);
  } else {
    fn();
  }
}

runWhenReady(function () {
  const sendBtn = document.getElementById('send-btn');
  if (sendBtn) sendBtn.addEventListener('click', sendMessage);
  const input = document.getElementById('message-input');
  if (input) {
    input.addEventListener('keydown', (e) => {
      // textarea：回车发送，Shift+回车换行
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
      }
    });
  }
  init();
  setupDocPanel();   // 文档管理面板（design v2.5 §六 / AC-1~22）
});

/* ================================================================
   renderSystemContent（design v2.5 §六 / AC-4 / 修 M6）
   将 [text](url) Markdown 链接渲染为 <a>，仅 doc_event 类型走链接渲染；
   presence 类（init/end/lost/reactivated）无害，直接 textContent。
   URL 白名单：http(s):// 开头，否则当纯文本，防 javascript: XSS。
   ================================================================ */
function renderSystemContent(msg) {
  // presence 类（无 message_type 或非 doc_event）：纯文本
  if (!msg || msg.message_type !== 'doc_event') {
    return escapeHtml(msg && msg.content ? msg.content : '');
  }
  const content = msg.content || '';
  // 匹配 [text](url) 链接
  return escapeHtml(content).replace(
    /\[([^\]]+)\]\((https?:\/\/[^)]+)\)/g,
    '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>'
  );
}

/* ================================================================
   ☰ 下拉菜单 + 文档管理面板（design v2.5 §六 / AC-1~22）
   ================================================================ */
function setupPanelMenu() {
  const toggle = document.getElementById('panel-toggle');
  const menu = document.getElementById('panel-menu');
  const agentPanel = document.getElementById('agent-panel');
  const docPanel = document.getElementById('doc-panel');
  if (!toggle || !menu) return;

  toggle.addEventListener('click', (e) => {
    e.stopPropagation();
    const isHidden = menu.classList.contains('hidden');
    menu.classList.toggle('hidden');
    if (!isHidden) return;
    // 关闭其他面板
    agentPanel.classList.add('hidden');
    docPanel.classList.add('hidden');
  });

  // 点击其他区域关闭菜单
  document.addEventListener('click', (e) => {
    if (!menu.contains(e.target) && !toggle.contains(e.target)) {
      menu.classList.add('hidden');
    }
  });

  // Agent 管理 → 显示已有 agent-panel
  const menuAgent = document.getElementById('menu-agent-mgmt');
  if (menuAgent) {
    menuAgent.addEventListener('click', () => {
      menu.classList.add('hidden');
      agentPanel.classList.remove('hidden');
      loadPanelAgents();
    });
  }

  // 文档管理 → 显示 doc-panel
  const menuDoc = document.getElementById('menu-doc-mgmt');
  if (menuDoc) {
    menuDoc.addEventListener('click', () => {
      menu.classList.add('hidden');
      docPanel.classList.remove('hidden');
      loadDocList();
    });
  }

  // agent-panel 关闭按钮
  const agentClose = document.getElementById('panel-close');
  if (agentClose) {
    agentClose.addEventListener('click', () => agentPanel.classList.add('hidden'));
  }

  // doc-panel 关闭按钮
  const docClose = document.getElementById('doc-panel-close');
  if (docClose) {
    docClose.addEventListener('click', () => docPanel.classList.add('hidden'));
  }
}

// 当前选中文档 id（用于编辑/保存）
let _currentDocId = null;
// 当前文档是否为纯文本（可编辑）
let _currentDocEditable = false;
// 原始文档内容（用于取消）
let _origDocContent = '';

// ---- 文件图标映射 ----
const _MIME_ICONS = {
  'text/plain': '📄',
  'text/markdown': '📝',
  'application/json': '{ }',
  'text/csv': '📊',
  'application/pdf': '📕',
  'image/png': '🖼',
  'image/jpeg': '🖼',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document': '📃',
};

function _docIcon(mime) {
  return _MIME_ICONS[mime] || '📎';
}

function _fmtSize(bytes) {
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / 1024 / 1024).toFixed(1) + ' MB';
}

function _fmtTime(iso) {
  if (!iso) return '';
  try {
    return new Date(iso.replace(' ', 'T')).toLocaleString('zh-CN', { hour: '2-digit', minute: '2-digit', month: '2-digit', day: '2-digit' });
  } catch (e) { return iso; }
}

// ---- 文档列表渲染 ----
async function loadDocList() {
  const list = document.getElementById('doc-list');
  const searchInput = document.getElementById('doc-search-input');
  if (!list) return;
  const q = searchInput ? searchInput.value.trim().toLowerCase() : '';
  list.innerHTML = '<div style="text-align:center;color:#bbb;font-size:12px;padding:12px">加载中…</div>';
  try {
    const res = await fetch(API_BASE + 'api/docs?limit=200');
    const data = await res.json();
    const docs = (data.docs || []).filter(d => !q || d.name.toLowerCase().includes(q));
    if (docs.length === 0) {
      list.innerHTML = '<div style="text-align:center;color:#bbb;font-size:12px;padding:12px">暂无文档</div>';
      return;
    }
    list.innerHTML = '';
    docs.forEach(doc => {
      const item = document.createElement('div');
      item.className = 'doc-item' + (_currentDocId === doc.id ? ' selected' : '');
      item.dataset.id = doc.id;
      item.innerHTML =
        '<span class="doc-item-icon">' + _docIcon(doc.mime) + '</span>' +
        '<div class="doc-item-info">' +
          '<div class="doc-item-name" title="' + escapeHtml(doc.name) + '">' + escapeHtml(doc.name) + '</div>' +
          '<div class="doc-item-meta">' +
            '<span class="doc-owner-badge ' + escapeHtml(doc.owner_type) + '">' + escapeHtml(doc.owner_type === 'agent' ? 'agent' : 'user') + '</span> ' +
            escapeHtml(doc.owner) + ' · ' + _fmtSize(doc.size) + ' · ' + _fmtTime(doc.updated_at) +
          '</div>' +
        '</div>' +
        '<div class="doc-item-actions">' +
          (doc.editable
            ? '<button class="doc-btn btn-edit">编辑</button>'
            : '<button class="doc-btn btn-down">下载</button>') +
        '</div>';
      // 点击行：选中并加载详情
      item.addEventListener('click', (e) => {
        if (e.target.tagName === 'BUTTON') return;
        openDoc(doc.id, doc.editable);
      });
      // 下载按钮
      const downBtn = item.querySelector('.btn-down');
      if (downBtn) {
        downBtn.addEventListener('click', (e) => {
          e.stopPropagation();
          window.open(doc.url, '_blank');
        });
      }
      // 编辑按钮
      const editBtn = item.querySelector('.btn-edit');
      if (editBtn) {
        editBtn.addEventListener('click', (e) => {
          e.stopPropagation();
          openDoc(doc.id, true);
        });
      }
      list.appendChild(item);
    });
  } catch (e) {
    list.innerHTML = '<div style="text-align:center;color:#e5484d;font-size:12px;padding:12px">加载失败</div>';
  }
}

function _setDocEditorVisible(visible) {
  const area = document.getElementById('doc-editor-area');
  if (!area) return;
  if (visible) {
    area.classList.remove('hidden');
  } else {
    area.classList.add('hidden');
    _currentDocId = null;
  }
}

function _showDocError(msg) {
  const status = document.getElementById('doc-upload-status');
  if (status) {
    status.textContent = msg;
    status.className = 'doc-upload-status err';
    setTimeout(() => {
      if (status) { status.textContent = ''; status.className = 'doc-upload-status'; }
    }, 4000);
  }
}

function _showDocOk(msg) {
  const status = document.getElementById('doc-upload-status');
  if (status) {
    status.textContent = msg;
    status.className = 'doc-upload-status ok';
    setTimeout(() => {
      if (status) { status.textContent = ''; status.className = 'doc-upload-status'; }
    }, 3000);
  }
}

async function openDoc(docId, editable) {
  _currentDocId = docId;
  _currentDocEditable = editable;
  const area = document.getElementById('doc-editor-area');
  if (!area) return;

  // 更新选中高亮
  document.querySelectorAll('.doc-item').forEach(el => {
    el.classList.toggle('selected', el.dataset.id === docId);
  });

  const nameEl = document.getElementById('doc-editor-name');
  const textEl = document.getElementById('doc-editor-text');
  const previewEl = document.getElementById('doc-editor-preview');
  const tabEdit = document.getElementById('doc-tab-edit');
  const tabPreview = document.getElementById('doc-tab-preview');
  const changeLog = document.getElementById('doc-change-log');

  try {
    const res = await fetch(API_BASE + 'api/docs/' + encodeURIComponent(docId));
    if (!res.ok) throw new Error('加载失败');
    const doc = await res.json();

    if (nameEl) nameEl.textContent = doc.name;
    _setDocEditorVisible(true);

    if (editable) {
      // 纯文本：加载内容到编辑器
      if (textEl) textEl.style.display = 'block';
      if (previewEl) previewEl.classList.add('hidden');
      if (tabEdit) tabEdit.classList.add('active');
      if (tabPreview) tabPreview.classList.remove('active');
      try {
        const dlRes = await fetch(doc.url);
        const text = await dlRes.text();
        _origDocContent = text;
        if (textEl) textEl.value = text;
        _renderPreview(text);
      } catch (e2) {
        _origDocContent = '';
        if (textEl) textEl.value = '';
        if (previewEl) previewEl.innerHTML = '<p style="color:#e5484d">内容加载失败</p>';
      }
    } else {
      // 二进制：只下载
      if (textEl) textEl.style.display = 'none';
      if (previewEl) previewEl.classList.remove('hidden');
      if (tabEdit) tabEdit.classList.remove('active');
      if (tabPreview) tabPreview.classList.add('active');
      if (previewEl) previewEl.innerHTML = '<p style="color:#888">此文件不支持在线预览，请 <a href="' + doc.url + '" target="_blank">下载</a></p>';
      if (textEl) textEl.value = '';
      _origDocContent = '';
    }

    // 改动记录
    if (changeLog) {
      const changes = doc.changes || [];
      if (changes.length === 0) {
        changeLog.innerHTML = '<div style="color:#bbb;font-size:11px">暂无改动记录</div>';
      } else {
        changeLog.innerHTML = changes.slice().reverse().map(c =>
          '<div class="doc-change-item"><span class="doc-change-action">' + escapeHtml(c.action) + '</span> ' +
          escapeHtml(c.actor) + ' <span class="doc-change-time">' + _fmtTime(c.created_at) + '</span>' +
          (c.summary ? ' — ' + escapeHtml(c.summary) : '') + '</div>'
        ).join('');
      }
    }
  } catch (e) {
    _setDocEditorVisible(false);
    _showDocError('加载文档失败');
  }
}

function _renderPreview(text) {
  const previewEl = document.getElementById('doc-editor-preview');
  if (!previewEl) return;
  try {
    if (typeof marked !== 'undefined') {
      const html = marked.parse(text || '');
      previewEl.innerHTML = html;
      if (typeof hljs !== 'undefined') {
        previewEl.querySelectorAll('pre code').forEach(block => {
          hljs.highlightElement(block);
        });
      }
    } else {
      // fallback：无 marked.js → 转义显示
      previewEl.innerHTML = '<pre style="white-space:pre-wrap;word-break:break-all;">' + escapeHtml(text) + '</pre>';
    }
  } catch (e) {
    previewEl.innerHTML = '<pre style="white-space:pre-wrap;">' + escapeHtml(text) + '</pre>';
  }
}

async function saveDoc() {
  const docId = _currentDocId;
  const textEl = document.getElementById('doc-editor-text');
  const content = textEl ? textEl.value : '';
  if (!docId) return;
  try {
    const res = await fetch(API_BASE + 'api/docs/' + encodeURIComponent(docId), {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content: content }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || '保存失败');
    }
    _origDocContent = content;
    _showDocOk('已保存');
    // 刷新列表
    loadDocList();
  } catch (e) {
    _showDocError('保存失败: ' + e.message);
  }
}

function cancelDocEdit() {
  const textEl = document.getElementById('doc-editor-text');
  if (textEl) textEl.value = _origDocContent;
  _setDocEditorVisible(false);
  _currentDocId = null;
}

function setupDocPanel() {
  // ☰ 菜单初始化
  setupPanelMenu();

  // 上传按钮
  const uploadBtn = document.getElementById('doc-upload-btn');
  const fileInput = document.getElementById('doc-file-input');
  if (uploadBtn && fileInput) {
    uploadBtn.addEventListener('click', () => fileInput.click());
    fileInput.addEventListener('change', async () => {
      if (!fileInput.files || fileInput.files.length === 0) return;
      const file = fileInput.files[0];
      const overwriteSel = document.getElementById('doc-overwrite-select');
      const docId = overwriteSel ? overwriteSel.value : '';
      await doUploadDoc(file, docId);
      fileInput.value = '';
    });
  }

  // 新建文档按钮
  const newBtn = document.getElementById('doc-new-btn');
  if (newBtn) {
    newBtn.addEventListener('click', async () => {
      const nameInput = document.getElementById('doc-new-name');
      const name = nameInput ? nameInput.value.trim() : '';
      if (!name) { _showDocError('请输入文件名'); return; }
      try {
        const res = await fetch(API_BASE + 'api/docs', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: name, content: '' }),
        });
        if (!res.ok) {
          const err = await res.json().catch(() => ({}));
          throw new Error(err.detail || '创建失败');
        }
        const doc = await res.json();
        _showDocOk('已创建: ' + name);
        if (nameInput) nameInput.value = '';
        await loadDocList();
        openDoc(doc.id, true);
      } catch (e) {
        _showDocError('创建失败: ' + e.message);
      }
    });
  }

  // 刷新按钮
  const reloadBtn = document.getElementById('doc-reload-btn');
  if (reloadBtn) reloadBtn.addEventListener('click', loadDocList);

  // 搜索过滤
  const searchInput = document.getElementById('doc-search-input');
  if (searchInput) {
    searchInput.addEventListener('input', loadDocList);
  }

  // 编辑/预览切换
  const tabEdit = document.getElementById('doc-tab-edit');
  const tabPreview = document.getElementById('doc-tab-preview');
  const textEl = document.getElementById('doc-editor-text');
  const previewEl = document.getElementById('doc-editor-preview');

  function switchTab(tab) {
    if (tab === 'edit') {
      if (tabEdit) tabEdit.classList.add('active');
      if (tabPreview) tabPreview.classList.remove('active');
      if (textEl) textEl.style.display = 'block';
      if (previewEl) previewEl.classList.add('hidden');
    } else {
      if (tabEdit) tabEdit.classList.remove('active');
      if (tabPreview) tabPreview.classList.add('active');
      if (textEl) textEl.style.display = 'none';
      if (previewEl) {
        previewEl.classList.remove('hidden');
        if (textEl) _renderPreview(textEl.value);
      }
    }
  }
  if (tabEdit) tabEdit.addEventListener('click', () => switchTab('edit'));
  if (tabPreview) tabPreview.addEventListener('click', () => switchTab('preview'));

  // 保存按钮
  const saveBtn = document.getElementById('doc-editor-save');
  if (saveBtn) saveBtn.addEventListener('click', saveDoc);

  // 取消按钮
  const cancelBtn = document.getElementById('doc-editor-cancel');
  if (cancelBtn) cancelBtn.addEventListener('click', cancelDocEdit);

  // 填充覆盖下拉（加载已有文档列表）
  async function loadOverwriteOptions() {
    const sel = document.getElementById('doc-overwrite-select');
    if (!sel) return;
    try {
      const res = await fetch(API_BASE + 'api/docs?limit=200');
      const data = await res.json();
      sel.innerHTML = '<option value="">不覆盖（新建）</option>';
      (data.docs || []).forEach(doc => {
        const opt = document.createElement('option');
        opt.value = doc.id;
        opt.textContent = doc.name + ' (' + doc.owner_type + ' ' + doc.owner + ')';
        sel.appendChild(opt);
      });
    } catch (e) { /* ignore */ }
  }
  loadOverwriteOptions();
}

async function doUploadDoc(file, docId) {
  const formData = new FormData();
  formData.append('file', file);
  if (docId) formData.append('doc_id', docId);
  try {
    const res = await fetch(API_BASE + 'api/docs/upload', {
      method: 'POST',
      body: formData,
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || '上传失败');
    }
    const data = await res.json();
    _showDocOk((data.action === 'overwrite' ? '已覆盖: ' : '已上传: ') + data.name);
    await loadDocList();
  } catch (e) {
    _showDocError('上传失败: ' + e.message);
  }
}
