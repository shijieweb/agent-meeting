let currentAgentList = [];

async function init() {
  await loadAgents();
  await loadHistory();
  await loadAgentStatus();
  setInterval(loadHistory, 2000); // 每2秒轮询历史消息
  setInterval(loadAgentStatus, 3000); // 每3秒刷新阿编在线状态（pull 即心跳）
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

async function loadHistory() {
  const res = await fetch('/api/messages/history');
  const data = await res.json();
  renderMessages(data.messages);
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

function renderMessages(messages) {
  const list = document.getElementById('message-list');
  list.innerHTML = '';
  messages.forEach(msg => {
    const bubble = document.createElement('div');
    bubble.classList.add('message-bubble');
    if (msg.sender_type === 'user') {
      bubble.classList.add('user');
      bubble.textContent = msg.content;
    } else {
      bubble.classList.add('agent');
      bubble.innerHTML = '<div class="agent-name">' + escapeHtml(msg.sender_agent_name) + ':</div>' + renderMarkdown(msg.content);
    }
    list.appendChild(bubble);

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
      list.appendChild(status);
    }
  });
  list.scrollTop = list.scrollHeight;
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
  await fetch('/api/messages/send', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  });
  input.value = '';
  await loadHistory();
}

document.getElementById('send-btn').addEventListener('click', sendMessage);
document.getElementById('message-input').addEventListener('keypress', (e) => {
  if (e.key === 'Enter') sendMessage();
});

init();
