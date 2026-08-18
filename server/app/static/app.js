let currentAgentList = [];

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
  await loadAgents();
  await loadInitialPage();      // 首屏只取一页（?limit=30），取代全量 loadHistory
  await loadAgentStatus();
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

// presence（服务端权威三态 online/lost/offline）：统一在线判定。
// 优先用 /api/agents/status 返回的 presence 字段；fallback（旧缓存 JS / 兼容）按 last_seen ≤1200s。
function isOnline(a) {
  if (a.presence !== undefined) return a.presence === 'online';
  if (!a.last_seen) return false;
  return a.status !== 'offline' &&
         (Date.now() - new Date(a.last_seen).getTime()) / 1000 <= 1200;
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
      if (a.status === 'working') {
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
  // 系统消息（presence_event）：灰色居中提示（.sys-notice，复用现有样式），不走 msg-row 布局（AC-3.4/4.1/4.2）
  if (msg.sender_type === 'system') {
    const notice = document.createElement('div');
    notice.className = 'sys-notice';
    notice.textContent = msg.content || '';
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
});
