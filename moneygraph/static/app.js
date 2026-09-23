'use strict';
const $ = id => document.getElementById(id);
const fmt = (n, digits=0) => Number(n).toLocaleString('ru-RU', {maximumFractionDigits:digits});
const money = n => fmt(n, 0);
const el = (tag, cls, text) => {const x=document.createElement(tag); if(cls)x.className=cls; if(text!==undefined)x.textContent=text; return x;};
let data, nodes=new Map(), selected=null, focus=false, colorMode='role', rowLimit=20, selectionVersion=0;
let visible=[], visibleEdges=[], localPositions=new Map(), view={scale:60,x:0,y:0}, graphSize={w:1,h:1}, drag=null, moved=false;
const canvas=$('graph'), ctx=canvas.getContext('2d');
function notice(text) {$('notice').textContent=text; $('notice').hidden=!text;}
async function api(path, options) {
  const response=await fetch(path, options); const body=await response.json();
  if(!response.ok)throw new Error(body.error||`Ошибка ${response.status}`); return body;
}
function color(n) {return colorMode==='role'?n.color:`hsl(${(n.cluster_id*137.508+130)%360} 35% 52%)`;}
function gidButton(gid) {const b=el('button','gid-link',gid); b.addEventListener('click',()=>selectNode(gid,true)); return b;}
function showTab(name) {
  for(const b of document.querySelectorAll('.tab'))b.classList.toggle('active',b.dataset.tab===name);
  for(const name2 of ['network','clusters','method'])$(`${name2}-view`).hidden=name!==name2;
  if(name==='network')requestAnimationFrame(resize);
}
document.querySelectorAll('.tab').forEach(b=>b.addEventListener('click',()=>showTab(b.dataset.tab)));
$('rail-network').onclick=()=>showTab('network');
$('rail-assistant').onclick=()=>{showTab('network');$('assistant-panel').scrollIntoView({behavior:'smooth',block:'center'});$('question').focus();};

async function load() {
  data=await api('/api/data'); nodes=new Map(data.nodes.map(n=>[n.gid,n]));
  selected=null;focus=false;rowLimit=20;
  const m=data.meta;
  $('stat-nodes').textContent=fmt(m.n_nodes);$('stat-seed').textContent=`${fmt(m.n_seed)} исходно известных клиентов`;
  $('stat-edges').textContent=fmt(m.n_edges);$('stat-tx').textContent=`${fmt(m.n_transactions)} транзакций`;
  $('stat-volume').textContent=m.turnover>=1e6?`${fmt(m.turnover/1e6,2)} млн`:money(m.turnover);
  $('stat-clusters').textContent=fmt(m.n_clusters);$('stat-components').textContent=`${m.n_multiseed_clusters} с несколькими seed · ${m.n_active_components} компонент со связями`;
  $('period').textContent=m.period.join(' — ');
  $('runtime').textContent=`Расчёт ${fmt(m.elapsed_seconds,2)} с · ${m.dataset_hash.slice(0,8)}`;
  $('dataset-badge').textContent=m.demo?'СИНТЕТИЧЕСКОЕ ДЕМО':'ЗАГРУЖЕННЫЙ ДАТАСЕТ';$('dataset-badge').classList.toggle('real',!m.demo);
  $('role-filter').replaceChildren(el('option','', 'Все роли'));$('role-filter').firstChild.value='';
  for(const [role,label] of Object.entries(m.roles)){const o=el('option','',label);o.value=role;$('role-filter').append(o);}
  const boundaryOption=el('option','','Граница наблюдения');boundaryOption.value='boundary';$('role-filter').append(boundaryOption);
  $('cluster-filter').replaceChildren(el('option','','Все кластеры'));$('cluster-filter').firstChild.value='';
  for(const c of data.clusters){const o=el('option','',`Кластер ${c.cluster_id} · ${c.n_nodes}`);o.value=c.cluster_id;$('cluster-filter').append(o);}
  $('legend').replaceChildren();
  for(const [role,label] of Object.entries(m.roles)){const s=el('span');const dot=el('i');dot.style.background=m.colors[role];s.append(dot,document.createTextNode(label));$('legend').append(s);}
  const boundaryLegend=el('span'),boundaryDot=el('i');boundaryDot.style.background=m.colors.boundary;boundaryLegend.append(boundaryDot,document.createTextNode('Граница (роль не определена)'));$('legend').append(boundaryLegend,el('span','','Белая точка = seed'));
  $('quality').replaceChildren(el('p','',m.limitations));
  const list=el('ul');m.warnings.forEach(w=>list.append(el('li','',w)));$('quality').append(list,el('p','',`Хеш набора: ${m.dataset_hash}. Результат детерминирован при одинаковых версиях библиотек и данных.`));
  renderClusters();filterGraph();renderRanking();resize();fit();
  await status();
  if(data.nodes.length){
    const first=data.nodes.find(n=>n.role==='consolidator'&&!n.is_seed)||data.nodes[0];
    $('question').placeholder=`Например: справка по ${first.gid}`;
    if(data.nodes.length>500){selected=first.gid;focus=true;filterGraph();fit();}
    await selectNode(first.gid);
  }
}
async function status() {
  const s=await api('/api/status');
  $('ai-status').textContent=s.configured?`${s.provider} настроен · вызовов ${s.calls}/${s.max_calls}`:'Локальный режим · API-ключ не задан';
  $('use-ai').disabled=!s.configured;
  if(!s.configured)$('use-ai').checked=false;
  $('use-ai').title=s.configured?s.model:'Настройте NVIDIA_BASE_URL для Brev или NVIDIA_API_KEY для API Catalog';
}
function filteredRows() {return data.nodes.filter(n=>(!$('role-filter').value||($('role-filter').value==='boundary'?n.truncated_by_depth:n.role===$('role-filter').value))&&($('cluster-filter').value===''||n.cluster_id===Number($('cluster-filter').value)));}
function filterGraph() {
  let allowed=new Set(filteredRows().map(n=>n.gid));
  if(focus&&selected){const neighbors=new Set([selected]);for(const e of data.edges){if(e.src===selected)neighbors.add(e.dst);if(e.dst===selected)neighbors.add(e.src);}allowed=new Set([...allowed].filter(id=>neighbors.has(id)));}
  visible=data.nodes.filter(n=>allowed.has(n.gid));visibleEdges=data.edges.filter(e=>allowed.has(e.src)&&allowed.has(e.dst));
  localPositions=new Map();
  if(focus&&selected){
    localPositions.set(selected,{x:0,y:0});
    const incoming=new Set(visibleEdges.filter(e=>e.dst===selected).map(e=>e.src));
    const left=visible.filter(n=>n.gid!==selected&&incoming.has(n.gid));
    const right=visible.filter(n=>n.gid!==selected&&!incoming.has(n.gid));
    for(const [list,x] of [[left,-2.6],[right,2.6]])list.forEach((n,i)=>localPositions.set(n.gid,{x,y:(i-(list.length-1)/2)*.7}));
  }
  $('graph-count').textContent=`${fmt(visible.length)} из ${fmt(data.meta.n_nodes)} узлов · ${fmt(visibleEdges.length)} связей${focus?' · окружение узла':''}`;
  const toggle=$('toggle-neighborhood');if(toggle)toggle.textContent=focus?'Показать всю сеть':'Только соседи';
  document.body.classList.toggle('focus',focus);draw();
}
for(const id of ['role-filter','cluster-filter'])$(id).addEventListener('change',()=>{focus=false;rowLimit=20;filterGraph();renderRanking();fit();});
$('by-role').onclick=()=>{colorMode='role';$('by-role').classList.add('active');$('by-cluster').classList.remove('active');draw();};
$('by-cluster').onclick=()=>{colorMode='cluster';$('by-cluster').classList.add('active');$('by-role').classList.remove('active');draw();};
function resize(){const rect=canvas.getBoundingClientRect();if(!rect.width)return;const changed=graphSize.w!==rect.width||graphSize.h!==rect.height;graphSize={w:rect.width,h:rect.height};const dpr=window.devicePixelRatio||1;canvas.width=rect.width*dpr;canvas.height=rect.height*dpr;ctx.setTransform(dpr,0,0,dpr,0,0);if(changed&&data)fit();else draw();}
new ResizeObserver(resize).observe(canvas.parentElement);
function fit() {
  if(!visible.length)return draw();
  const xs=visible.map(n=>(localPositions.get(n.gid)||n).x),ys=visible.map(n=>(localPositions.get(n.gid)||n).y),minX=Math.min(...xs),maxX=Math.max(...xs),minY=Math.min(...ys),maxY=Math.max(...ys);
  view.scale=Math.min((graphSize.w-110)/Math.max(.8,maxX-minX),(graphSize.h-100)/Math.max(.8,maxY-minY));
  view.x=graphSize.w/2-(minX+maxX)/2*view.scale;view.y=graphSize.h/2-(minY+maxY)/2*view.scale;draw();
}
function pos(n){const p=localPositions.get(n.gid)||n;return {x:p.x*view.scale+view.x,y:p.y*view.scale+view.y};}
function radius(n){return 3.4+Math.sqrt(n.priority_score)*5.5;}
function draw() {
  ctx.clearRect(0,0,graphSize.w,graphSize.h);if(!data)return;
  const connected=new Set([selected]);for(const e of visibleEdges){if(e.src===selected)connected.add(e.dst);if(e.dst===selected)connected.add(e.src);}
  for(const e of visibleEdges){const a=pos(nodes.get(e.src)),b=pos(nodes.get(e.dst));const highlight=selected&&(e.src===selected||e.dst===selected);ctx.strokeStyle=highlight?'#48856b':'#bccbc0';ctx.globalAlpha=highlight?.82:.38;ctx.lineWidth=highlight?1.3:.65;
    const angle=Math.atan2(b.y-a.y,b.x-a.x),r=radius(nodes.get(e.dst))+2,tx=b.x-Math.cos(angle)*r,ty=b.y-Math.sin(angle)*r;
    ctx.beginPath();ctx.moveTo(a.x,a.y);ctx.lineTo(tx,ty);ctx.stroke();
    if(Math.hypot(b.x-a.x,b.y-a.y)>r+10){const size=highlight?5:3.5;ctx.beginPath();ctx.moveTo(tx,ty);ctx.lineTo(tx-Math.cos(angle-.5)*size,ty-Math.sin(angle-.5)*size);ctx.lineTo(tx-Math.cos(angle+.5)*size,ty-Math.sin(angle+.5)*size);ctx.closePath();ctx.fillStyle=ctx.strokeStyle;ctx.fill();}
  }
  ctx.globalAlpha=1;
  const list=[...visible].sort((a,b)=>(a.gid===selected?1:0)-(b.gid===selected?1:0));
  for(const n of list){const p=pos(n),r=radius(n);ctx.globalAlpha=selected&&!connected.has(n.gid)?.65:1;
    if(n.gid===selected){ctx.beginPath();ctx.arc(p.x,p.y,r+5,0,Math.PI*2);ctx.fillStyle='#d7e8d7';ctx.fill();ctx.strokeStyle='#357d5e';ctx.lineWidth=1;ctx.stroke();}
    ctx.beginPath();ctx.arc(p.x,p.y,r,0,Math.PI*2);ctx.fillStyle=color(n);ctx.fill();ctx.strokeStyle='white';ctx.lineWidth=n.is_seed?2:1;ctx.stroke();
    if(n.is_seed){ctx.beginPath();ctx.arc(p.x,p.y,1.4,0,Math.PI*2);ctx.fillStyle='white';ctx.fill();}
    if(n.gid===selected||visible.length<25){ctx.globalAlpha=1;ctx.font=n.gid===selected?'600 10px Segoe UI':'9px Segoe UI';ctx.textAlign='center';ctx.fillStyle='#486053';ctx.fillText(n.gid,p.x,p.y+r+13);}
  }
  ctx.globalAlpha=1;
  if(!visible.length){ctx.textAlign='center';ctx.font='12px Segoe UI';ctx.fillStyle='#89988d';ctx.fillText('Нет узлов с такими фильтрами',graphSize.w/2,graphSize.h/2);}
}
function zoom(factor,x=graphSize.w/2,y=graphSize.h/2){const next=Math.max(8,Math.min(3500,view.scale*factor));const ratio=next/view.scale;view.x=x-(x-view.x)*ratio;view.y=y-(y-view.y)*ratio;view.scale=next;draw();}
canvas.addEventListener('wheel',e=>{e.preventDefault();const r=canvas.getBoundingClientRect();zoom(e.deltaY<0?1.12:.89,e.clientX-r.left,e.clientY-r.top);},{passive:false});
canvas.addEventListener('pointerdown',e=>{drag={x:e.clientX,y:e.clientY,vx:view.x,vy:view.y};moved=false;canvas.setPointerCapture(e.pointerId);});
canvas.addEventListener('pointermove',e=>{if(!drag)return;const dx=e.clientX-drag.x,dy=e.clientY-drag.y;moved=moved||Math.hypot(dx,dy)>4;view.x=drag.vx+dx;view.y=drag.vy+dy;draw();});
canvas.addEventListener('pointerup',e=>{if(!moved){const r=canvas.getBoundingClientRect(),x=e.clientX-r.left,y=e.clientY-r.top;const hit=[...visible].reverse().find(n=>{const p=pos(n);return Math.hypot(x-p.x,y-p.y)<radius(n)+5;});if(hit)selectNode(hit.gid);}drag=null;});
canvas.addEventListener('pointercancel',()=>{drag=null;});
$('zoom-in').onclick=()=>zoom(1.3);$('zoom-out').onclick=()=>zoom(1/1.3);$('fit').onclick=()=>{focus=false;filterGraph();fit();};
$('search-form').onsubmit=e=>{e.preventDefault();selectNode($('search').value.trim(),true);};

async function selectNode(gid, center=false) {
  gid=String(gid);if(!nodes.has(gid)){notice(`Узел ${gid} не найден в текущем наборе`);return;}
  notice('');selected=gid;const version=++selectionVersion;
  if(!visible.some(n=>n.gid===gid)){ $('role-filter').value='';$('cluster-filter').value='';focus=false;filterGraph(); }
  if(focus)filterGraph();
  if(center){showTab('network');const n=localPositions.get(gid)||nodes.get(gid);view.x=graphSize.w/2-n.x*view.scale;view.y=graphSize.h/2-n.y*view.scale;}
  draw();renderRanking();
  try {const details=await api('/api/node?gid='+encodeURIComponent(gid));if(version!==selectionVersion)return;renderNode(details);}catch(e){notice(e.message);}
}
function renderNode(d) {
  const n=d.node, card=$('node-card');card.replaceChildren();
  card.append(el('h3','node-id',`#${n.gid}`),el('div','node-meta',`Кластер ${n.cluster_id} · колено ${n.depth}${n.is_seed?' · исходный seed':''}`));
  const pill=el('span','role-pill',n.truncated_by_depth?'Граница · роль не определена':data.meta.roles[n.role]);pill.style.color=n.truncated_by_depth?data.meta.colors.boundary:data.meta.colors[n.role];card.append(pill,el('div','evidence',n.evidence));
  const metrics=el('div','node-metrics');
  for(const [label,value,unit] of [['Входящий объём',money(n.sum_in),'KZT'],['Исходящий объём',money(n.sum_out),'KZT'],['Отправители',n.in_degree,'клиентов'],['Получатели',n.out_degree,'клиентов'],['Входящие переводы',n.in_tx,'операций'],['Исходящие переводы',n.out_tx,'операций']]){const cell=el('div');cell.append(el('small','',label),el('strong','',value),el('em','',` ${unit}`));metrics.append(cell);}card.append(metrics);
  card.append(el('div','node-meta',`PageRank: ${n.pagerank.toFixed(6)} · отдал/получил: ${n.pass_ratio===null?'нет входящих':n.pass_ratio.toFixed(3)}`));
  card.append(el('div','node-section-title',`Приоритет ${n.priority_score.toFixed(3)} · признаки роли ${n.role_score.toFixed(2)}`));
  const names={volume:'Объём',seed_reach:'Связь с seed',activity:'Число переводов',bridge:'Посредничество',role:'Признаки роли'};
  for(const [key,value] of Object.entries(n.priority_factors)){const row=el('div','factor'),track=el('div','track'),fill=el('div','fill');fill.style.width=(value/.3*100)+'%';track.append(fill);row.append(el('span','',names[key]),track,el('span','',value.toFixed(3)));card.append(row);}
  card.append(el('div','node-section-title','Путь от ближайшего seed'));
  const path=el('div','path');d.seed_path.forEach((v,i)=>{if(i)path.append(document.createTextNode(' → '));path.append(gidButton(v));});if(!d.seed_path.length)path.textContent='Нет направленного пути до 4 переходов';card.append(path);
  if(n.depth===4)card.append(el('p','node-warning','Граница выборки: исходящий поток может быть обрезан.'));
  else if(n.is_seed)card.append(el('p','node-warning','Входящие seed неполны; отношение потоков не определяет роль.'));
  const buttons=el('div','node-buttons'),neighbors=el('button','',focus?'Показать всю сеть':'Только соседи'),report=el('button','','Составить справку ↗');
  neighbors.id='toggle-neighborhood';
  neighbors.onclick=()=>{focus=!focus;filterGraph();fit();neighbors.textContent=focus?'Показать всю сеть':'Только соседи';};report.onclick=()=>ask(`Справка по узлу ${n.gid}`);buttons.append(neighbors,report);card.append(buttons);
  const details=el('details');details.style.marginTop='15px';details.append(el('summary','','Переводы и метрики'));const detailText=el('pre');detailText.style.cssText='white-space:pre-wrap;max-height:220px;overflow:auto;font-size:9px';detailText.textContent=JSON.stringify(d,null,2);details.append(detailText);card.append(details);
}
function renderRanking() {
  if(!data)return;const rows=filteredRows();$('ranking').replaceChildren();
  rows.slice(0,rowLimit).forEach((n,i)=>{const tr=el('tr',n.gid===selected?'selected':'');tr.append(el('td','',String(i+1)));const id=el('td');id.append(gidButton(n.gid));tr.append(id);
    const role=el('td'),dot=el('span','role-dot');dot.style.background=n.color;role.append(dot,document.createTextNode(n.truncated_by_depth?'Граница наблюдения':data.meta.roles[n.role]));tr.append(role);
    const score=el('td'),wrap=el('div','score'),track=el('i'),fill=el('b');fill.style.width=n.priority_score*100+'%';track.append(fill);wrap.append(document.createTextNode(n.priority_score.toFixed(2)),track);score.append(wrap);tr.append(score,el('td','',n.evidence));
    tr.onclick=e=>{if(e.target.tagName!=='BUTTON')selectNode(n.gid,true);};$('ranking').append(tr);
  });$('table-count').textContent=`Показано ${Math.min(rowLimit,rows.length)} из ${rows.length}`;$('more-rows').hidden=rowLimit>=rows.length;
}
$('more-rows').onclick=()=>{rowLimit+=20;renderRanking();};
function renderClusters() {
  $('cluster-cards').replaceChildren();for(const c of data.clusters){const card=el('article','cluster-card');card.append(el('h3','',`Кластер ${c.cluster_id}`),el('p','',`${c.n_nodes} участников · ${c.n_seed} seed`),el('strong','',`${money(c.sum_kzt_internal)} KZT`),el('p','',c.hypothesis));const b=el('button','','Открыть на графе ↗');b.onclick=()=>{showTab('network');$('role-filter').value='';$('cluster-filter').value=c.cluster_id;focus=false;filterGraph();renderRanking();fit();};card.append(b);$('cluster-cards').append(card);}
}
function linkedText(container,text,allowed) {
  let start=0;const regex=/\[gid:(-?\d+)\]/g;let match;
  while((match=regex.exec(text))){container.append(document.createTextNode(text.slice(start,match.index)));container.append(allowed.has(match[1])&&nodes.has(match[1])?gidButton(match[1]):document.createTextNode(match[0]));start=regex.lastIndex;}
  container.append(document.createTextNode(text.slice(start)));
}
async function ask(question) {
  if($('send-question').disabled)return;showTab('network');$('assistant-panel').scrollIntoView({behavior:'smooth',block:'nearest'});
  const chat=$('chat');chat.querySelector('.chat-welcome')?.remove();const user=el('div','message user');user.append(el('span','msg-label','ВАШ ВОПРОС'),document.createTextNode(question));chat.append(user);
  const pending=el('div','message','Проверяю данные…');chat.append(pending);$('send-question').disabled=true;chat.scrollTop=chat.scrollHeight;
  try{const reply=await api('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question,selected,use_ai:$('use-ai').checked})});pending.replaceChildren(el('span','msg-label',`${reply.mode==='nvidia'?`${reply.provider||'API'} · ОТВЕТ ПО ГРАФУ`:'ЛОКАЛЬНЫЙ РАСЧЁТ'}${reply.cached?' · КЭШ':''}`));linkedText(pending,reply.text,new Set(reply.references));if(reply.warning)pending.append(el('p','warning',reply.warning));
    if(reply.results.length){const details=el('details');details.append(el('summary','','Проверить результаты инструментов'),el('pre','',JSON.stringify(reply.results,null,2)));pending.append(details);}
    await status();
  }catch(e){pending.textContent=e.message;}finally{$('send-question').disabled=false;chat.scrollTop=chat.scrollHeight;}
}
$('chat-form').onsubmit=e=>{e.preventDefault();const q=$('question').value.trim();if(q){$('question').value='';ask(q);}};
$('question').onkeydown=e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();$('chat-form').requestSubmit();}};
$('ask-card').onclick=()=>{if(selected)ask(`Справка по узлу ${selected}`);else notice('Сначала выберите узел');};
$('ask-top').onclick=()=>ask('Кого смотреть первым? Покажи топ приоритетов');
$('upload').onchange=async e=>{
  const file=e.target.files[0];if(!file)return;notice('Загрузка и пересчёт…');e.target.disabled=true;
  try{await api('/api/upload',{method:'POST',headers:{'Content-Type':'application/zip'},body:file});$('chat').replaceChildren(el('div','chat-welcome','Загружен новый набор. Задайте вопрос по его данным.'));await load();notice('Данные загружены. Все выгрузки обновлены.');}catch(err){notice(err.message);}finally{e.target.disabled=false;e.target.value='';}
};
load().catch(e=>notice(`Не удалось загрузить приложение: ${e.message}`));
