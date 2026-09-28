const $ = id => document.getElementById(id);
const bridge = window.AstrBotPluginPage;
let catalogue = [], brands = {}, current = null, images = {}, dirty = false, busy = false;
let birthdayLayouts = {}, birthdayColumns = '1';
function status(text, error = false) { $('status').textContent = text; $('status').classList.toggle('error', error); }
function changed() { dirty = true; $('dirty').textContent = '有未保存的修改'; }
async function run(action) {
  if (busy) return;
  busy = true; document.body.classList.add('busy'); $('fields').disabled = true;
  try { await action(); } catch (error) { status(error.message || '操作失败，请查看 AstrBot 日志。', true); }
  finally { busy = false; document.body.classList.remove('busy'); $('fields').disabled = false; }
}
function maySwitch() {
  if (dirty) { status('请先保存修改，或点击“撤销未保存修改”后切换角色。', true); return false; }
  return !busy;
}
function renderList() {
  const query = $('search').value.trim().toLocaleLowerCase().replace(/\s/g, '');
  const rows = catalogue.filter(row => [row.name, row.name_jp, ...(row.aliases || [])].some(s => String(s).toLocaleLowerCase().replace(/\s/g, '').includes(query)));
  $('count').textContent = `${rows.length} 位角色`;
  $('characters').replaceChildren();
  for (const row of rows) {
    const button = document.createElement('button'); button.type = 'button';
    button.classList.toggle('active', current?.name === row.name);
    const title = document.createElement('strong'); title.textContent = row.display_name || row.name;
    const sub = document.createElement('small'); sub.textContent = `${row.name_jp} · ${row.birthday || '生日未登记'}${row.custom ? ' · 已编辑' : ''}`;
    button.append(title, sub); button.onclick = () => { if (maySwitch()) run(() => select(row.name)); };
    $('characters').append(button);
  }
}
async function refreshList() {
  const result = await bridge.apiGet('editor/list'); catalogue = result.characters; brands = result.brands;
  birthdayLayouts = result.birthday_layouts;
  $('storage').textContent = `手动资料和图片保存于：${result.storage}`;
  $('sources').textContent = `生日基础数据：${result.birthday_source}；角色头像基础数据：${result.idol_source}。`;
  renderList();
}
async function select(name) {
  const data = await bridge.apiGet('editor/detail', {name});
  await fill(data); status('已载入角色。修改会在保存后生效。');
}
async function fill(data) {
  current = data; dirty = false;
  $('empty').hidden = true; $('form').hidden = false; $('preview_area').hidden = !!data.isNew;
  $('previews').replaceChildren(); $('title').textContent = data.isNew ? '新增角色' : (data.display_name || data.name);
  $('origin').textContent = data.isNew ? '本地新增' : data.record?.revision ? '手动资料' : '基础资料';
  $('name').value = data.name || ''; $('name').readOnly = !data.isNew;
  $('name_jp').value = data.record?.name_jp || ''; $('name_jp').required = !!data.isNew;
  $('base_name').textContent = data.isNew ? '担当名片上显示的名字' : `当前显示：${data.name_jp}`;
  $('brand').replaceChildren(new Option(`沿用基础资料${data.brand ? ' · ' + brands[data.brand] : ''}`, ''));
  for (const [key, label] of Object.entries(brands)) $('brand').add(new Option(label, key));
  $('brand').value = data.record?.brand || '';
  $('aliases').value = (data.record?.aliases || []).join(', ');
  $('source_note').value = data.record?.source_note || '';
  const birthday = data.record?.birthday;
  $('birthday_mode').value = birthday === undefined ? 'inherit' : birthday ? 'custom' : 'none';
  $('birthday').value = birthday || data.base_birthday || '';
  $('birthday').disabled = $('birthday_mode').value !== 'custom';
  $('base_birthday').textContent = `基础资料：${data.base_birthday || '未登记生日'}`;
  $('dirty').textContent = '修改后点击保存生效';
  images = {}; $('image_editors').replaceChildren();
  for (const kind of ['birthday', 'tantou']) await makeImageEditor(kind, data.images?.[kind] || {});
  renderList();
}
async function loadImage(uri) {
  if (!uri) return null;
  const image = new Image(); image.src = uri;
  await image.decode(); return image;
}
async function makeImageEditor(kind, initial) {
  const state = {source: initial.source || '', x: initial.x ?? .5, y: initial.y ?? .5, zoom: initial.zoom ?? 1,
    image: await loadImage(initial.image), action: 'keep'};
  images[kind] = state;
  const card = document.createElement('section'); card.className = 'image-editor';
  // Only constant strings enter this template; character data uses textContent.
  card.innerHTML = `<h3>${kind === 'birthday' ? '生日卡图片' : '担当头像'}</h3><div class="stage"><canvas aria-label="拖动调整图片位置"></canvas></div><input type="file" accept="image/png,image/jpeg,image/webp,image/gif" aria-label="上传${kind === 'birthday' ? '生日卡图片' : '担当头像'}"><small>支持 PNG / JPEG / WebP / GIF，最大 10 MB。GIF 使用第一帧。</small><label class="row">缩放<input type="range" data-key="zoom" min="1" max="4" step="0.01"></label><label class="row">水平位置<input type="range" data-key="x" min="0" max="1" step="0.001"></label><label class="row">垂直位置<input type="range" data-key="y" min="0" max="1" step="0.001"></label><div class="reset"><button type="button" data-action="center">居中 / 原始缩放</button><button type="button" data-action="restore">恢复原有来源</button></div><small class="image-note"></small>`;
  $('image_editors').append(card);
  const canvas = card.querySelector('canvas'), ctx = canvas.getContext('2d');
  let layoutSelect;
  const dimensions = document.createElement('small');
  card.querySelector('.stage').after(dimensions);
  if (kind === 'birthday') {
    const label = document.createElement('label'); label.textContent = '生日卡布局';
    layoutSelect = document.createElement('select'); layoutSelect.dataset.previewOnly = 'true';
    for (const [columns, layout] of Object.entries(birthdayLayouts)) {
      layoutSelect.add(new Option(`${columns} 列 · ${layout.item_width} × ${layout.portrait_height}`, columns));
    }
    layoutSelect.value = birthdayColumns;
    label.append(layoutSelect); card.querySelector('.stage').before(label);
    layoutSelect.onchange = () => {birthdayColumns = layoutSelect.value; $('previews').replaceChildren(); draw();};
  }
  const note = card.querySelector('.image-note');
  function draw() {
    const layout = birthdayLayouts[birthdayColumns];
    canvas.width = kind === 'birthday' ? layout.item_width : 500;
    canvas.height = kind === 'birthday' ? layout.portrait_height : 450;
    // Set both CSS dimensions together so narrow screens cannot distort the crop.
    const displayWidth = Math.min(canvas.width, 265 * canvas.width / canvas.height);
    canvas.style.width = `${displayWidth}px`; canvas.style.height = 'auto';
    dimensions.textContent = kind === 'birthday' ? '按实际图片框比例缩小显示；多人布局预览会重复当前角色。' : '与实际担当头像共用官网圆角轮廓。';
    const w = canvas.width, h = canvas.height;
    ctx.clearRect(0, 0, w, h); ctx.save();
    ctx.fillStyle = kind === 'birthday' ? '#ffffff' : '#f3f5f8'; ctx.fillRect(0,0,w,h);
    if (state.image) {
      const iw=state.image.naturalWidth, ih=state.image.naturalHeight;
      const sw=Math.min(iw, ih*w/h)/state.zoom, sh=sw*h/w;
      ctx.drawImage(state.image,(iw-sw)*state.x,(ih-sh)*state.y,sw,sh,0,0,w,h);
    } else { ctx.fillStyle='#788599'; ctx.font='22px system-ui'; ctx.textAlign='center'; ctx.fillText('上传一张图片',w/2,h/2); }
    if (kind === 'tantou') {
      ctx.globalCompositeOperation = 'destination-in';
      ctx.drawImage($('tantou-mask'), 0, 0, w, h);
    }
    ctx.restore();
    for (const slider of card.querySelectorAll('[data-key]')) slider.value=state[slider.dataset.key];
    note.textContent = state.action === 'restore' ? '保存后恢复原有图片来源。' : state.action === 'change' ? '裁切已调整，保存后生效。' : initial.custom ? '已使用手动图片。' : '当前为原有来源；未调整时保留原样。';
  }
  function update() { state.action='change'; changed(); draw(); }
  for (const slider of card.querySelectorAll('[data-key]')) slider.oninput=()=>{if (!state.image) return; state[slider.dataset.key]=Number(slider.value); update();};
  card.querySelector('[type=file]').onchange = event => run(async()=>{
    const file=event.target.files[0]; if (!file) return;
    if(file.size>10*1024*1024) throw Error('图片不能超过 10 MB。');
    const result=await bridge.upload('editor/upload',file);
    state.image=await loadImage(result.image); state.source=result.source; state.x=state.y=.5; state.zoom=1; update();
    status('图片已载入，可拖动调整。保存后才会用于群消息。');
  });
  card.querySelector('[data-action=center]').onclick=()=>{if(!state.image)return;state.x=state.y=.5;state.zoom=1;update();};
  card.querySelector('[data-action=restore]').onclick=()=>{state.action='restore';changed();draw();};
  let drag=null;
  canvas.onpointerdown=event=>{if(!state.image||busy)return;canvas.setPointerCapture(event.pointerId);drag={x:event.clientX,y:event.clientY,sx:state.x,sy:state.y};};
  canvas.onpointermove=event=>{
    if(!drag)return;
    const rect=canvas.getBoundingClientRect(), iw=state.image.naturalWidth,ih=state.image.naturalHeight;
    const sw=Math.min(iw,ih*canvas.width/canvas.height)/state.zoom,sh=sw*canvas.height/canvas.width;
    state.x=iw>sw?Math.max(0,Math.min(1,drag.sx-(event.clientX-drag.x)/rect.width*sw/(iw-sw))):.5;
    state.y=ih>sh?Math.max(0,Math.min(1,drag.sy-(event.clientY-drag.y)/rect.height*sh/(ih-sh))):.5;
    update();
  };
  canvas.onpointerup=canvas.onpointercancel=()=>{drag=null;};
  draw();
}
$('search').oninput=renderList;
$('form').addEventListener('input',event=>{if(event.target.type!=='file' && !event.target.dataset.previewOnly)changed();});
$('birthday_mode').onchange=()=>{$('birthday').disabled=$('birthday_mode').value!=='custom';changed();};
$('new').onclick=()=>{if(maySwitch())run(()=>fill({isNew:true,name:'',record:{},images:{}}));};
$('discard').onclick=()=>run(async()=>{if(current.isNew)await fill({isNew:true,name:'',record:{},images:{}});else await select(current.name);});
$('form').onsubmit=event=>{
  event.preventDefault(); run(async()=>{
    const changes={};
    for(const [kind,state] of Object.entries(images)) {
      if(state.action==='restore')changes[kind]=null;
      else if(state.action==='change')changes[kind]={source:state.source,x:state.x,y:state.y,zoom:state.zoom};
    }
    const mode=$('birthday_mode').value;
    const result=await bridge.apiPost('editor/save',{name:$('name').value.trim(),revision:current.revision||'',
      name_jp:$('name_jp').value.trim(),brand:$('brand').value,aliases:$('aliases').value.split(/[,，\n]/).map(x=>x.trim()).filter(Boolean),
      birthday:mode==='inherit'?null:mode==='none'?'':$('birthday').value.trim(),source_note:$('source_note').value,images:changes});
    dirty=false; await refreshList(); await select(result.name); status('已保存。新的生日资料和图片立即生效。');
  });
};
$('preview').onclick=()=>run(async()=>{
  if(dirty)throw Error('请先保存修改，再生成实际名片预览。');
  status('正在生成名片预览…');const result=await bridge.apiGet('editor/preview',{name:current.name,columns:birthdayColumns});
  $('previews').replaceChildren();
  for(const [kind,label] of [['birthday','生日卡'],['tantou','担当名片']]) {
    const figure=document.createElement('div'), title=document.createElement('p'), image=document.createElement('img');
    title.textContent=label;image.src=result[kind];image.alt=label;figure.append(title,image);$('previews').append(figure);
  }
  status(`预览已生成。${result.date_note}`);
});
window.addEventListener('beforeunload',event=>{if(dirty){event.preventDefault();event.returnValue='';}});
if(!bridge)status('请从 AstrBot 插件详情里的“角色图片与资料”页面进入。',true);
else run(async()=>{await bridge.ready();await $('tantou-mask').decode();await refreshList();status('选择角色开始编辑，或点击右上角新增角色。');});
