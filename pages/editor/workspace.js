const modules = ['characters', 'music'], guards = {};
let active = 'characters';
function mount(name) {
  if (guards[name]) return;
  const host = document.getElementById(`${name}-panel`), root = host.attachShadow({mode:'open'});
  root.append(document.getElementById(`${name}-template`).content.cloneNode(true));
  guards[name] = window.ImasEditors[name](root, window.AstrBotPluginPage);
}
for (const name of modules) document.getElementById(`${name}-tab`).onclick = () => {
  if (active === name || !guards[active]()) return;
  active = name;
  for (const key of modules) {
    document.getElementById(`${key}-panel`).hidden = key !== name;
    document.getElementById(`${key}-tab`).setAttribute('aria-selected', String(key === name));
  }
  mount(name);
};
mount(active);
