const $ = id => document.getElementById(id), bridge = window.AstrBotPluginPage;
let catalogue = [], current = null, imageState = null, dirty = false, busy = false, page = 0;
const labels = {utaite: '唱见', music_creator: 'P主／音乐创作者'}, pageSize = 80;
function status(value, error = false) { $('status').textContent = value; $('status').classList.toggle('error', error); }
function changed() { dirty = true; $('dirty').textContent = '有未保存的修改'; $('preview-image').hidden = true; }
async function run(action) {
  if (busy) return;
  busy = true; document.body.classList.add('busy'); $('fields').disabled = true;
  try { await action(); } catch (error) { status(error.message || '操作失败，请重试。', true); }
  finally { busy = false; document.body.classList.remove('busy'); $('fields').disabled = false; }
}
function normal(value) { return String(value).normalize('NFKC').toLocaleLowerCase().replace(/\s/g, ''); }
function renderList() {
  const query = normal($('search').value), kind = $('kind').value;
  const rows = catalogue.filter(row => (!kind || row.kind === kind) && (!$('missing').checked || (!row.has_image && !row.has_source_image)) && (!$('no-birthday').checked || !row.birthday) && [row.name, ...row.aliases].some(value => normal(value).includes(query)));
  const pages = Math.max(1, Math.ceil(rows.length / pageSize)); page = Math.min(page, pages - 1);
  $('count').textContent = `${rows.length} 位人物`; $('page').textContent = `${page + 1} / ${pages}`;
  $('previous').disabled = page === 0; $('next').disabled = page + 1 >= pages;
  $('list').replaceChildren();
  for (const row of rows.slice(page * pageSize, (page + 1) * pageSize)) {
    const button = document.createElement('button'), title = document.createElement('strong'), sub = document.createElement('small');
    button.type = 'button'; button.classList.toggle('active', current?.id === row.id);
    title.textContent = row.name; sub.textContent = `${labels[row.kind]} · ${row.birthday || '生日未登记'}${row.custom ? ' · 已编辑' : ''}`;
    button.append(title, sub); button.onclick = () => {
      if (dirty) { status('请先保存修改，或撤销后再切换人物。', true); return; }
      run(() => select(row.id));
    }; $('list').append(button);
  }
}
async function refresh() {
  const result = await bridge.apiGet('people/list'); catalogue = result.people;
  $('storage').textContent = `手动资料和上传图片保存于：${result.storage}`; renderList();
}
async function loadImage(uri) { if (!uri) return null; const img = new Image(); img.src = uri; await img.decode(); return img; }
async function select(id) {
  status('正在载入人物资料与头像…');
  const data = await bridge.apiGet('people/detail', {name: id});
  current = data; dirty = false; $('empty').hidden = true; $('form').hidden = false;
  $('preview-area').hidden = $('source-area').hidden = false; $('preview-image').hidden = true;
  $('title').textContent = data.name; $('badge').textContent = labels[data.kind];
  $('name').value = data.name; $('base-name').textContent = `基础资料：${data.base_name}`;
  $('aliases').value = (data.record.aliases || []).join('\n'); $('note').value = data.record.source_note || '';
  $('birthday-mode').value = data.record.birthday === undefined ? 'inherit' : data.record.birthday ? 'custom' : 'none';
  $('birthday').value = data.birthday || ''; $('birthday').disabled = $('birthday-mode').value !== 'custom';
  $('base-birthday').textContent = `基础资料：${data.base_birthday || '未公开／未登记'}`;
  $('sources').replaceChildren();
  for (const source of [...new Set([data.birthday_source, data.image_source, ...data.sources])]) {
    try { const url = new URL(source); if (url.protocol !== 'https:' && url.protocol !== 'http:') continue;
      const a = document.createElement('a'); a.textContent = source; a.href = url.href; a.target = '_blank'; a.rel = 'noopener noreferrer'; $('sources').append(a);
    } catch { /* A manual source note is plain text, not a link. */ }
  }
  imageState = {...data.image, image: await loadImage(data.image.image), action: 'keep'};
  $('moegirl-image').hidden = !data.has_moegirl_image;
  $('dirty').textContent = '修改后点击保存生效'; draw(); renderList(); status('已载入资料，修改保存后立即生效。');
}
function draw() {
  if (!imageState) return;
  const state = imageState, canvas = $('canvas'), ctx = canvas.getContext('2d'), w = canvas.width, h = canvas.height;
  ctx.clearRect(0, 0, w, h); ctx.save(); ctx.fillStyle = '#f3f5f8'; ctx.fillRect(0, 0, w, h);
  if (state.image) {
    const iw = state.image.naturalWidth, ih = state.image.naturalHeight;
    if (state.action === 'keep' && !state.custom) {
      const scale = Math.min(w * .64 / iw, h * .8 / ih); ctx.drawImage(state.image, (w - iw * scale) / 2, (h - ih * scale) / 2, iw * scale, ih * scale);
    } else if (state.fit === 'contain') {
      const scale = Math.min(w / iw, h / ih) * state.zoom, dw = Math.max(1, Math.round(iw * scale)), dh = Math.max(1, Math.round(ih * scale));
      ctx.drawImage(state.image, Math.round((w - dw) * state.x), Math.round((h - dh) * state.y), dw, dh);
    } else {
      const sw = Math.min(iw, ih * w / h) / state.zoom, sh = sw * h / w;
      ctx.drawImage(state.image, (iw - sw) * state.x, (ih - sh) * state.y, sw, sh, 0, 0, w, h);
    }
  } else { ctx.fillStyle = '#737f91'; ctx.textAlign = 'center'; ctx.font = '24px system-ui'; ctx.fillText('上传一张图片', w / 2, h / 2); }
  ctx.globalCompositeOperation = 'destination-in'; ctx.drawImage($('mask'), 0, 0, w, h); ctx.restore();
  for (const field of ['fit', 'zoom', 'x', 'y']) $(field).value = state[field];
  $('image-note').textContent = state.action === 'restore' ? '保存后恢复目录来源图片。' : state.action === 'change' ? '位置已调整，保存后用于担当名片。' : '当前与实际头像一致，拖动或使用滑块调整。';
}
for (const id of ['search', 'kind', 'missing', 'no-birthday']) $(id).addEventListener('input', () => { page = 0; renderList(); });
$('previous').onclick = () => { page--; renderList(); }; $('next').onclick = () => { page++; renderList(); };
$('form').addEventListener('input', event => { if (event.target.type !== 'file') changed(); });
$('birthday-mode').onchange = () => { $('birthday').disabled = $('birthday-mode').value !== 'custom'; };
for (const field of ['fit', 'zoom', 'x', 'y']) $(field).oninput = () => {
  if (!imageState?.image) return;
  imageState[field] = field === 'fit' ? $(field).value : Number($(field).value);
  if (imageState.fit === 'cover' && imageState.zoom < 1) imageState.zoom = 1;
  imageState.action = 'change'; changed(); draw();
};
$('upload').onchange = event => run(async () => {
  const file = event.target.files[0]; if (!file) return;
  if (file.size > 10 * 1024 * 1024) throw Error('图片不能超过 10 MB。');
  const result = await bridge.upload('people/upload', file);
  imageState = {image: await loadImage(result.image), source: result.source, x: .5, y: .5, zoom: 1, fit: 'contain', action: 'change', custom: true};
  changed(); draw(); status('图片已载入，可以拖动调整位置。');
});
$('center').onclick = () => { if (!imageState?.image) return; imageState.x = imageState.y = .5; imageState.zoom = 1; imageState.action = 'change'; changed(); draw(); };
$('moegirl-image').onclick = () => run(async () => {
  status('正在载入萌百图片…');
  const result = await bridge.apiGet('people/source', {name: current.id});
  imageState = {image: await loadImage(result.image), source: result.source, x: .5, y: .5, zoom: 1, fit: 'contain', action: 'change', custom: true};
  changed(); draw(); status('萌百图片已载入，调整后点击保存生效。');
});
$('restore').onclick = () => { imageState.action = 'restore'; changed(); draw(); };
let drag = null;
$('canvas').onpointerdown = event => { if (!imageState?.image || busy) return; $('canvas').setPointerCapture(event.pointerId); drag = {px: event.clientX, py: event.clientY, x: imageState.x, y: imageState.y}; };
$('canvas').onpointermove = event => {
  if (!drag) return;
  const state = imageState, rect = $('canvas').getBoundingClientRect(), iw = state.image.naturalWidth, ih = state.image.naturalHeight;
  const scale = (state.fit === 'contain' ? Math.min(600 / iw, 540 / ih) : Math.max(600 / iw, 540 / ih)) * state.zoom;
  const dx = 600 - iw * scale, dy = 540 - ih * scale;
  state.x = Math.abs(dx) > .1 ? Math.max(0, Math.min(1, drag.x + (event.clientX - drag.px) * 600 / rect.width / dx)) : .5;
  state.y = Math.abs(dy) > .1 ? Math.max(0, Math.min(1, drag.y + (event.clientY - drag.py) * 540 / rect.height / dy)) : .5;
  state.action = 'change'; changed(); draw();
};
$('canvas').onpointerup = $('canvas').onpointercancel = () => { drag = null; };
$('discard').onclick = () => run(() => select(current.id));
$('form').onsubmit = event => { event.preventDefault(); run(async () => {
  const state = imageState, mode = $('birthday-mode').value;
  const payload = {id: current.id, revision: current.record.revision || '', name: $('name').value.trim(), aliases: $('aliases').value.split(/[,，\n]/).map(x => x.trim()).filter(Boolean),
    birthday: mode === 'inherit' ? null : mode === 'none' ? '' : $('birthday').value.trim(), source_note: $('note').value};
  if (state.action === 'restore') payload.image = null;
  if (state.action === 'change') payload.image = {source: state.source, fit: state.fit, zoom: state.zoom, x: state.x, y: state.y};
  const result = await bridge.apiPost('people/save', payload); dirty = false; await refresh(); await select(result.id); status('已保存，担当名片立即使用新的图片和名字。');
}); };
$('preview').onclick = () => run(async () => {
  if (dirty) throw Error('请先保存修改，再预览实际名片。');
  status('正在生成名片…'); const result = await bridge.apiGet('people/preview', {name: current.id});
  $('preview-image').src = result.tantou; $('preview-image').hidden = false; status('预览已生成。');
});
window.addEventListener('beforeunload', event => { if (dirty) { event.preventDefault(); event.returnValue = ''; } });
if (!bridge) status('请从 AstrBot 插件详情中的「唱见与P主图片资料」进入。', true);
else run(async () => { await bridge.ready(); await $('mask').decode(); await refresh(); status('选择一位唱见或音乐创作者开始编辑。'); });
