(function () {
  'use strict';

  const S = window.SECCAP || {};

  async function api(method, url, body) {
    const res = await fetch(url, {
      method: method,
      headers: {'Content-Type': 'application/json', 'X-CSRF-Token': S.csrf},
      body: body === undefined ? undefined : JSON.stringify(body)
    });
    let data = {};
    try { data = await res.json(); } catch (e) { }
    return {ok: res.ok, status: res.status, data: data};
  }
  window.api = api;

  let toastTimer = null;
  function toast(msg) {
    const el = document.getElementById('toast');
    if (!el) return;
    el.textContent = msg;
    el.style.display = 'block';
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { el.style.display = 'none'; }, 2600);
  }
  window.toast = toast;

  function fmt(sec) {
    sec = Math.max(0, Math.floor(sec));
    const m = Math.floor(sec / 60), s = sec % 60;
    return m + ':' + (s < 10 ? '0' : '') + s;
  }
  window.fmtTime = fmt;

  window.Countdown = function (el, track) {
    let left = 0, total = 1, running = false;
    function paint() {
      el.textContent = fmt(left);
      const low = left <= 300, out = left <= 0;
      el.classList.toggle('low', low && !out);
      el.classList.toggle('out', out);
      if (track) {
        track.classList.toggle('low', low && !out);
        track.classList.toggle('out', out);
        const bar = track.firstElementChild;
        if (bar) bar.style.width = Math.max(0, Math.min(100, (left / total) * 100)) + '%';
      }
    }
    setInterval(function () { if (running && left > 0) { left -= 1; paint(); } }, 1000);
    return {
      sync: function (remaining, totalSeconds, isRunning) {
        left = remaining; total = totalSeconds || 1; running = !!isRunning; paint();
      }
    };
  };

  window.poll = function (url, handler) {
    let stop = false;
    async function tick() {
      if (stop) return;
      try {
        const r = await api('GET', url);
        if (r.ok) handler(r.data);
      } catch (e) { }
      setTimeout(tick, (document.hidden ? 4 : 1) * S.poll * 1000);
    }
    tick();
    document.addEventListener('visibilitychange', function () {
      if (!document.hidden) tick();
    });
    return {stop: function () { stop = true; }};
  };

  window.debounce = function (fn, ms) {
    let handle = null;
    return function () {
      const args = arguments, self = this;
      clearTimeout(handle);
      handle = setTimeout(function () { fn.apply(self, args); }, ms || 700);
    };
  };

  window.wireReorder = function (list, onChange) {
    list.addEventListener('click', function (ev) {
      const btn = ev.target.closest('[data-move]');
      if (!btn) return;
      const item = btn.closest('.orderitem');
      const dir = btn.getAttribute('data-move');
      if (dir === 'up' && item.previousElementSibling) {
        list.insertBefore(item, item.previousElementSibling);
      } else if (dir === 'down' && item.nextElementSibling) {
        list.insertBefore(item.nextElementSibling, item);
      }
      if (onChange) onChange(currentOrder(list));
    });

    let held = null, startY = 0, moved = false;

    list.addEventListener('pointerdown', function (ev) {
      if (ev.button !== undefined && ev.button !== 0) return;
      if (ev.target.closest('button, a, input, select, textarea')) return;
      const item = ev.target.closest('.orderitem');
      if (!item || !list.contains(item)) return;
      held = item;
      startY = ev.clientY;
      moved = false;
      item.classList.add('dragging');
      list.setPointerCapture(ev.pointerId);
    });

    list.addEventListener('pointermove', function (ev) {
      if (!held) return;
      if (!moved && Math.abs(ev.clientY - startY) < 4) return;
      moved = true;
      ev.preventDefault();
      const rows = [].slice.call(list.querySelectorAll('.orderitem'));
      for (let i = 0; i < rows.length; i++) {
        const other = rows[i];
        if (other === held) continue;
        const box = other.getBoundingClientRect();
        const middle = box.top + box.height / 2;
        if (ev.clientY < middle && other.compareDocumentPosition(held) &
            Node.DOCUMENT_POSITION_FOLLOWING) {
          list.insertBefore(held, other);
          return;
        }
        if (ev.clientY > middle && other.compareDocumentPosition(held) &
            Node.DOCUMENT_POSITION_PRECEDING) {
          list.insertBefore(held, other.nextSibling);
          return;
        }
      }
    });

    function drop(ev) {
      if (!held) return;
      held.classList.remove('dragging');
      held = null;
      try { list.releasePointerCapture(ev.pointerId); } catch (e) {}
      if (moved && onChange) onChange(currentOrder(list));
      moved = false;
    }
    list.addEventListener('pointerup', drop);
    list.addEventListener('pointercancel', drop);
  };

  function currentOrder(list) {
    return Array.prototype.map.call(list.querySelectorAll('.orderitem'),
                                    function (el) { return el.getAttribute('data-id'); });
  }
  window.currentOrder = currentOrder;

  document.addEventListener('click', function (ev) {
    const btn = ev.target.closest('[data-copy]');
    if (!btn) return;
    const text = btn.getAttribute('data-copy');
    if (navigator.clipboard) {
      navigator.clipboard.writeText(text).then(function () { toast(S.t.copied); });
    } else {
      const tmp = document.createElement('input');
      tmp.value = text;
      document.body.appendChild(tmp);
      tmp.select();
      try { document.execCommand('copy'); toast(S.t.copied); } catch (e) { }
      document.body.removeChild(tmp);
    }
  });

  window.renderJSON = function (node, value, key) {
    const wrap = document.createElement('div');
    if (key !== undefined && key !== null) {
      const k = document.createElement('span');
      k.className = 'k';
      k.textContent = key + ': ';
      wrap.appendChild(k);
    }
    if (value === null) {
      const v = document.createElement('span'); v.className = 'null'; v.textContent = 'null';
      wrap.appendChild(v);
    } else if (Array.isArray(value) || typeof value === 'object') {
      const det = document.createElement('details');
      det.open = true;
      const sum = document.createElement('summary');
      sum.textContent = Array.isArray(value) ? '[ ' + value.length + ' ]' : '{ … }';
      det.appendChild(sum);
      const entries = Array.isArray(value)
        ? value.map(function (v, i) { return [String(i), v]; })
        : Object.keys(value).map(function (k) { return [k, value[k]]; });
      entries.forEach(function (pair) { window.renderJSON(det, pair[1], pair[0]); });
      wrap.appendChild(det);
    } else {
      const v = document.createElement('span');
      v.className = typeof value === 'number' ? 'n' : (typeof value === 'boolean' ? 'b' : 's');
      v.textContent = typeof value === 'string' ? '"' + value + '"' : String(value);
      wrap.appendChild(v);
    }
    node.appendChild(wrap);
    return wrap;
  };

  let openDialog = null;

  window.openModal = function (build, opts) {
    opts = opts || {};
    if (openDialog) openDialog.close();

    const opener = document.activeElement;
    const back = document.createElement('div');
    back.className = 'modalback';
    const box = document.createElement('div');
    box.className = 'modal' + (opts.wide ? ' wide' : '') +
                    (opts.className ? ' ' + opts.className : '');
    box.setAttribute('role', 'dialog');
    box.setAttribute('aria-modal', 'true');

    function close() {
      if (!openDialog) return;
      document.removeEventListener('keydown', onKey);
      back.remove();
      document.body.classList.remove('modal-open');
      openDialog = null;
      if (opener && opener.focus) opener.focus();
      if (opts.onClose) opts.onClose();
    }

    function focusable() {
      return [].slice.call(box.querySelectorAll(
        'a[href],button:not([disabled]),input,select,textarea,[tabindex]:not([tabindex="-1"])'
      )).filter(function (e) { return e.offsetParent !== null; });
    }

    function onKey(e) {
      if (e.key === 'Escape') { close(); return; }
      if (e.key !== 'Tab') return;
      const f = focusable();
      if (!f.length) return;
      const first = f[0], last = f[f.length - 1];
      if (e.shiftKey && document.activeElement === first) { last.focus(); e.preventDefault(); }
      else if (!e.shiftKey && document.activeElement === last) { first.focus(); e.preventDefault(); }
    }

    build(box, close);

    const heading = box.querySelector('h2, h3');
    if (heading) {
      if (!heading.id) heading.id = 'modalh-' + Math.floor(performance.now());
      box.setAttribute('aria-labelledby', heading.id);
    }

    back.appendChild(box);
    back.addEventListener('mousedown', function (e) {
      if (e.target === back) close();
    });
    document.body.appendChild(back);
    document.body.classList.add('modal-open');
    document.addEventListener('keydown', onKey);
    openDialog = {close: close};

    const f = focusable();
    (opts.focus ? box.querySelector(opts.focus) : null || f[0] || box).focus();
    return close;
  };

  window.wireStuck = function (root, slug) {
    const scope = root.getAttribute('data-scope');
    const total = parseInt(root.getAttribute('data-total'), 10) || 0;
    const rungs = [].slice.call(root.querySelectorAll('[data-rung]'));
    const list = root.querySelector('[data-revealed]');
    const button = root.querySelector('[data-reveal]');
    const counter = root.querySelector('[data-count]');
    const done = root.querySelector('[data-exhausted]');
    let used = 0;

    function paint() {
      counter.textContent = counter.getAttribute('data-t')
        .replace('{used}', used).replace('{total}', total);
      if (used >= total) {
        button.hidden = true;
        done.hidden = false;
      } else if (used > 0) {
        button.textContent = button.getAttribute('data-t-more');
      }
    }

    function show(rung, text) {
      const card = document.createElement('div');
      card.className = 'hintcard';
      const head = document.createElement('div');
      head.className = 'hh';
      const n = document.createElement('span');
      n.className = 'n';
      n.textContent = String(used + 1);
      const lv = document.createElement('span');
      lv.className = 'lv';
      lv.textContent = rung.getAttribute('data-level');
      head.appendChild(n);
      head.appendChild(lv);
      const body = document.createElement('p');
      body.textContent = text;
      card.appendChild(head);
      card.appendChild(body);
      list.appendChild(card);
      rung.setAttribute('data-taken', '1');
    }

    async function take(rung) {
      const body = {mission_slug: slug, hint_id: rung.getAttribute('data-rung')};
      let r = await api('POST', '/api/hint', body);
      if (!r.ok && r.status === 409 && r.data && r.data.error === 'locked') {
        const opened = await api('POST', '/api/hint/next',
          scope.indexOf('q:') === 0 ? {mission_slug: slug}
                                    : {mission_slug: slug, artifact_id: scope});
        if (!opened.ok) return null;
        r = await api('POST', '/api/hint', body);
      }
      return r.ok ? r.data.hint : null;
    }

    button.addEventListener('click', async function () {
      const next = rungs.find(function (x) { return !x.hasAttribute('data-taken'); });
      if (!next) { used = total; paint(); return; }
      button.disabled = true;
      const hint = await take(next);
      button.disabled = false;
      if (!hint) { toast(S.t.error); return; }
      show(next, hint.text);
      used += 1;
      paint();
    });

    paint();

    root.restore = async function (takenIds) {
      const want = rungs.filter(function (x) {
        return takenIds.indexOf(x.getAttribute('data-rung')) !== -1
               && !x.hasAttribute('data-taken');
      });
      for (const rung of want) {
        const r = await api('POST', '/api/hint',
                            {mission_slug: slug, hint_id: rung.getAttribute('data-rung')});
        if (!r.ok || !r.data || !r.data.hint) continue;
        show(rung, r.data.hint.text);
        used += 1;
        paint();
      }
    };
  };

})();
