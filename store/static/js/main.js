/* ============================================================
   Newa Store — Main JS
   ============================================================ */
(function () {
  'use strict';

  const $ = (sel, ctx = document) => ctx.querySelector(sel);
  const $$ = (sel, ctx = document) => Array.from(ctx.querySelectorAll(sel));

  function getCookie(name) {
    const v = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)');
    return v ? v.pop() : '';
  }
  const csrftoken = getCookie('csrftoken') || ($('[name=csrfmiddlewaretoken]') || {}).value || '';

  // ---------- Theme toggle ----------
  const themeKey = 'newa-theme';
  const savedTheme = localStorage.getItem(themeKey);
  if (savedTheme) document.documentElement.setAttribute('data-theme', savedTheme);
  document.addEventListener('click', (e) => {
    const t = e.target.closest('[data-theme-toggle]');
    if (!t) return;
    const cur = document.documentElement.getAttribute('data-theme') === 'light' ? 'dark' : 'light';
    document.documentElement.setAttribute('data-theme', cur);
    localStorage.setItem(themeKey, cur);
    const icon = t.querySelector('i');
    if (icon) icon.className = cur === 'light' ? 'fas fa-moon' : 'fas fa-sun';
  });

  // ---------- Mobile nav ----------
  window.toggleNav = function () {
    const nav = $('.nav');
    if (nav) nav.classList.toggle('mobile-open');
  };

  // ---------- Auto-style Django form widgets ----------
  document.addEventListener('DOMContentLoaded', () => {
    $$('.form-group input, .form-group select, .form-group textarea, form.auth-form input, form.auth-form select').forEach((el) => {
      const t = (el.type || '').toLowerCase();
      if (t === 'hidden' || t === 'checkbox' || t === 'radio' || t === 'submit') return;
      if (!el.classList.contains('form-control')) el.classList.add('form-control');
    });
  });

  // ---------- Hero carousel ----------
  const slides = $$('.hero-slide');
  const dots = $$('.hero-dot');
  let heroIndex = 0;
  function showSlide(i) {
    if (!slides.length) return;
    slides.forEach((s, idx) => s.classList.toggle('active', idx === i));
    dots.forEach((d, idx) => d.classList.toggle('active', idx === i));
    heroIndex = i;
  }
  if (slides.length > 1) {
    setInterval(() => showSlide((heroIndex + 1) % slides.length), 6000);
    dots.forEach((d, i) => d.addEventListener('click', () => showSlide(i)));
  }

  // ---------- Alerts auto dismiss ----------
  $$('.alert').forEach((a) => {
    const close = $('.alert-close', a);
    if (close) close.addEventListener('click', () => a.remove());
    setTimeout(() => { a.style.opacity = '0'; setTimeout(() => a.remove(), 300); }, 5000);
  });

  // ---------- AJAX helper ----------
  async function post(url, data) {
    const body = data instanceof FormData ? data : new URLSearchParams(data);
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'X-CSRFToken': csrftoken, 'X-Requested-With': 'XMLHttpRequest' },
      body,
    });
    return res.json();
  }

  // ---------- Add to cart ----------
  document.addEventListener('submit', async (e) => {
    const form = e.target.closest('form[data-ajax-cart]');
    if (!form) return;
    e.preventDefault();
    const btn = form.querySelector('[type=submit]');
    const original = btn ? btn.innerHTML : '';
    if (btn) { btn.disabled = true; btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i>'; }
    try {
      const json = await post(form.action, new FormData(form));
      updateCartCount(json.cart_count);
      showToast(json.message || 'Added to cart', 'success');
      loadCartMini();
    } catch (err) {
      showToast('Something went wrong.', 'error');
    } finally {
      if (btn) { btn.disabled = false; btn.innerHTML = original; }
    }
  });

  // ---------- Wishlist toggle ----------
  document.addEventListener('click', async (e) => {
    const btn = e.target.closest('[data-wishlist-toggle]');
    if (!btn) return;
    e.preventDefault();
    if (btn.dataset.auth !== 'true') { window.location.href = btn.dataset.loginUrl; return; }
    try {
      const json = await post(btn.dataset.url, {});
      btn.classList.toggle('active', json.added);
      const icon = btn.querySelector('i');
      if (icon) icon.className = json.added ? 'fas fa-heart' : 'far fa-heart';
      showToast(json.message, 'success');
    } catch (err) {
      showToast('Login required.', 'error');
    }
  });

  // ---------- Quantity steppers ----------
  document.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-qty]');
    if (!btn) return;
    const wrap = btn.closest('.qty-control');
    const input = wrap && wrap.querySelector('input');
    if (!input) return;
    let val = parseInt(input.value || '1', 10);
    val += btn.dataset.qty === 'plus' ? 1 : -1;
    const min = parseInt(input.min || '1', 10);
    if (val < min) val = min;
    input.value = val;
    input.dispatchEvent(new Event('change', { bubbles: true }));
  });

  // ---------- Cart qty update ----------
  document.addEventListener('change', async (e) => {
    const input = e.target.closest('[data-cart-qty]');
    if (!input) return;
    const itemId = input.dataset.cartQty;
    const url = input.dataset.url;
    try {
      const json = await post(url, { quantity: input.value });
      updateCartCount(json.cart_count);
      $$('[data-line-total]').forEach((el) => {
        if (el.closest('[data-item=' + itemId + ']')) {
          const price = parseFloat(el.dataset.unit || '0');
          el.textContent = currency(price * parseInt(input.value, 10));
        }
      });
      const sub = $('#cart-subtotal'); if (sub) sub.textContent = currency(json.subtotal);
      const dis = $('#cart-discount'); if (dis) dis.textContent = '-' + currency(json.discount);
      const tot = $('#cart-total'); if (tot) tot.textContent = currency(json.total);
      loadCartMini();
    } catch (err) { /* noop */ }
  });

  // ---------- Cart drawer ----------
  window.openCart = function () {
    $('.drawer') && $('.drawer').classList.add('show');
    $('.drawer-overlay') && $('.drawer-overlay').classList.add('show');
    document.body.style.overflow = 'hidden';
    loadCartMini();
  };
  window.closeCart = function () {
    $('.drawer') && $('.drawer').classList.remove('show');
    $('.drawer-overlay') && $('.drawer-overlay').classList.remove('show');
    document.body.style.overflow = '';
  };
  window.addEventListener('click', (e) => {
    if (e.target.classList && e.target.classList.contains('drawer-overlay')) closeCart();
  });

  function updateCartCount(count) {
    if (count === undefined) return;
    $$('[data-cart-count]').forEach((el) => {
      el.textContent = count;
      el.style.display = count > 0 ? 'flex' : 'none';
    });
  }

  async function loadCartMini() {
    const box = $('#drawer-body');
    if (!box) return;
    try {
      const res = await fetch(box.dataset.url, { headers: { 'X-Requested-With': 'XMLHttpRequest' } });
      box.innerHTML = await res.text();
      const totals = box.querySelector('[data-mini-total]');
      if (totals) $('#drawer-foot-total') && ($('#drawer-foot-total').textContent = totals.dataset.miniTotal);
    } catch (err) { /* noop */ }
  }

  // ---------- Toasts ----------
  function showToast(message, type) {
    let wrap = $('#toast-wrap');
    if (!wrap) {
      wrap = document.createElement('div');
      wrap.id = 'toast-wrap';
      wrap.style.cssText = 'position:fixed;bottom:24px;right:24px;z-index:2000;display:flex;flex-direction:column;gap:10px';
      document.body.appendChild(wrap);
    }
    const el = document.createElement('div');
    el.className = 'alert alert-' + (type || 'info');
    const icon = type === 'error' ? 'fa-circle-exclamation' : 'fa-circle-check';
    el.innerHTML = `<i class="fas ${icon}"></i><span>${message}</span>`;
    wrap.appendChild(el);
    setTimeout(() => { el.style.opacity = '0'; setTimeout(() => el.remove(), 300); }, 3500);
  }
  window.showToast = showToast;

  function currency(v) {
    const n = parseFloat(v || 0);
    return 'Rs. ' + n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  // ---------- Tabs ----------
  document.addEventListener('click', (e) => {
    const btn = e.target.closest('.tab-btn');
    if (!btn) return;
    const nav = btn.closest('.tab-nav');
    const container = nav.parentElement;
    $$('.tab-btn', nav).forEach((b) => b.classList.remove('active'));
    $$('.tab-panel', container).forEach((p) => p.classList.remove('active'));
    btn.classList.add('active');
    const panel = container.querySelector('#' + btn.dataset.tab);
    if (panel) panel.classList.add('active');
  });

  // ---------- Product gallery ----------
  document.addEventListener('click', (e) => {
    const thumb = e.target.closest('.pd-thumb');
    if (!thumb) return;
    $$('.pd-thumb').forEach((t) => t.classList.remove('active'));
    thumb.classList.add('active');
    const main = $('#pd-main-img');
    if (main) main.src = thumb.dataset.src;
  });

  // ---------- Variant selection ----------
  document.addEventListener('click', (e) => {
    const opt = e.target.closest('[data-variant]');
    if (!opt) return;
    const group = opt.closest('.option-values');
    $$('[data-variant]', group).forEach((o) => o.classList.remove('active'));
    opt.classList.add('active');
    const hidden = $('#variant-id');
    if (hidden) hidden.value = opt.dataset.variant;
    const price = $('#pd-price');
    if (price && opt.dataset.price) price.textContent = currency(opt.dataset.price);
  });

  // ---------- Search suggestions ----------
  const searchInput = $('#search-input');
  const suggestions = $('#search-suggestions');
  let searchTimer;
  if (searchInput && suggestions) {
    searchInput.addEventListener('input', () => {
      clearTimeout(searchTimer);
      const q = searchInput.value.trim();
      if (q.length < 2) { suggestions.classList.remove('show'); return; }
      searchTimer = setTimeout(async () => {
        try {
          const res = await fetch(`/search/suggest/?q=${encodeURIComponent(q)}`, { headers: { 'X-Requested-With': 'XMLHttpRequest' } });
          const json = await res.json();
          if (!json.results || !json.results.length) { suggestions.classList.remove('show'); return; }
          suggestions.innerHTML = json.results.map((r) => {
            const img = r.image
              ? `<img src="${r.image}" alt="" loading="lazy">`
              : '';
            return `<a class="suggestion-item" href="${r.url}">
              ${img}
              <div><div style="font-weight:600;font-size:.88rem">${r.name}</div>
              <div style="color:var(--primary);font-weight:700;font-size:.85rem">${r.price}</div></div></a>`;
          }).join('');
          suggestions.classList.add('show');
        } catch (err) { /* noop */ }
      }, 250);
    });
    document.addEventListener('click', (e) => {
      if (!e.target.closest('.search-bar')) suggestions.classList.remove('show');
    });
  }

  // ---------- Newsletter ----------
  document.addEventListener('submit', async (e) => {
    const form = e.target.closest('form[data-newsletter]');
    if (!form) return;
    e.preventDefault();
    try {
      const json = await post(form.action, new FormData(form));
      showToast(json.message, json.success ? 'success' : 'error');
      if (json.success) form.reset();
    } catch (err) { showToast('Subscription failed.', 'error'); }
  });

  // ---------- Payment radio cards ----------
  document.addEventListener('change', (e) => {
    const radio = e.target.closest('.radio-card input[type=radio]');
    if (!radio) return;
    const name = radio.name;
    $$(`input[name="${name}"]`).forEach((r) => r.closest('.radio-card') && r.closest('.radio-card').classList.remove('selected'));
    radio.closest('.radio-card').classList.add('selected');
  });

  // ---------- Shipping toggle on checkout ----------
  const shippingRadios = $$('input[name="shipping_option"]');
  function toggleShipping() {
    const val = shippingRadios.find((r) => r.checked);
    const box = $('#shipping-address-box');
    if (box) box.style.display = val && val.value === 'different' ? 'block' : 'none';
  }
  shippingRadios.forEach((r) => r.addEventListener('change', toggleShipping));
  toggleShipping();

  // ---------- Countdown timers ----------
  $$('[data-countdown]').forEach((el) => {
    const end = new Date(el.dataset.countdown).getTime();
    const tick = () => {
      const diff = end - Date.now();
      if (diff <= 0) { el.textContent = 'Ended'; return; }
      const d = Math.floor(diff / 86400000);
      const h = Math.floor((diff % 86400000) / 3600000);
      const m = Math.floor((diff % 3600000) / 60000);
      const s = Math.floor((diff % 60000) / 1000);
      el.textContent = `${d}d ${h}h ${m}m ${s}s`;
    };
    tick();
    setInterval(tick, 1000);
  });
})();
