(function () {
  'use strict';

  const $ = (sel, ctx = document) => ctx.querySelector(sel);
  const $$ = (sel, ctx = document) => Array.from(ctx.querySelectorAll(sel));

  function getCookie(name) {
    const v = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)');
    return v ? v.pop() : '';
  }
  const csrftoken = getCookie('csrftoken') || ($('[name=csrfmiddlewaretoken]') || {}).value || '';

  const IMG_PLACEHOLDER = document.body && document.body.dataset.placeholderUrl;
  if (IMG_PLACEHOLDER) {
    document.addEventListener('error', (e) => {
      const el = e.target;
      if (el && el.tagName === 'IMG' && !el.dataset.imgFellBack) {
        el.dataset.imgFellBack = '1';
        el.src = IMG_PLACEHOLDER;
      }
    }, true);
  }

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

  window.toggleNav = function () {
    const nav = $('.nav');
    if (nav) nav.classList.toggle('mobile-open');
  };

  document.addEventListener('DOMContentLoaded', () => {
    $$('.form-group input, .form-group select, .form-group textarea, form.auth-form input, form.auth-form select').forEach((el) => {
      const t = (el.type || '').toLowerCase();
      if (t === 'hidden' || t === 'checkbox' || t === 'radio' || t === 'submit') return;
      if (!el.classList.contains('form-control')) el.classList.add('form-control');
    });
  });

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

  $$('.alert').forEach((a) => {
    const close = $('.alert-close', a);
    if (close) close.addEventListener('click', () => a.remove());
    setTimeout(() => { a.style.opacity = '0'; setTimeout(() => a.remove(), 300); }, 5000);
  });

  async function post(url, data) {
    const body = data instanceof FormData ? data : new URLSearchParams(data);
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'X-CSRFToken': csrftoken, 'X-Requested-With': 'XMLHttpRequest' },
      body,
    });
    return res.json();
  }

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
    } catch (err) {  }
  });

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
      const previous = parseInt(el.textContent, 10);
      el.textContent = count;
      el.style.display = count > 0 ? 'flex' : 'none';
      if (!isNaN(previous) && previous !== count) {
        el.classList.remove('pop');
        void el.offsetWidth;
        el.classList.add('pop');
      }
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
    } catch (err) {  }
  }

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
    const icons = {
      error: 'fa-circle-exclamation', success: 'fa-circle-check',
      warning: 'fa-triangle-exclamation', info: 'fa-circle-info',
    };
    el.innerHTML = `<i class="fas ${icons[type] || 'fa-circle-info'}"></i><span>${message}</span>`;
    wrap.appendChild(el);
    setTimeout(() => { el.style.opacity = '0'; setTimeout(() => el.remove(), 300); }, 3500);
  }
  window.showToast = showToast;

  function currency(v) {
    const n = parseFloat(v || 0);
    return 'Rs. ' + n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

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

  document.addEventListener('click', (e) => {
    const thumb = e.target.closest('.pd-thumb');
    if (!thumb) return;
    $$('.pd-thumb').forEach((t) => t.classList.remove('active'));
    thumb.classList.add('active');
    const main = $('#pd-main-img');
    if (main) main.src = thumb.dataset.src;
  });

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
        } catch (err) {  }
      }, 250);
    });
    document.addEventListener('click', (e) => {
      if (!e.target.closest('.search-bar')) suggestions.classList.remove('show');
    });
  }

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

  document.addEventListener('change', (e) => {
    const radio = e.target.closest('.radio-card input[type=radio]');
    if (!radio) return;
    const name = radio.name;
    $$(`input[name="${name}"]`).forEach((r) => r.closest('.radio-card') && r.closest('.radio-card').classList.remove('selected'));
    radio.closest('.radio-card').classList.add('selected');
  });

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

  const msgWrap = $('.messages-wrap');
  if (msgWrap && msgWrap.children.length) {
    const tw = $('#toast-wrap');
    if (tw) Array.from(msgWrap.children).forEach((a) => tw.appendChild(a));
  }

  const shopResults = $('#shop-results');
  const filterForm = $('#filter-form');
  const sortForm = $('#sort-form');
  let infiniteObserver = null;

  function shopStateUrl() {
    let params = new URLSearchParams();
    if (filterForm) params = new URLSearchParams(new FormData(filterForm));
    if (sortForm) {
      const sp = new URLSearchParams(new FormData(sortForm));
      const sort = sp.get('sort_by');
      if (sort) params.set('sort_by', sort);
    }
    params.delete('page');
    return '/shop/?' + params.toString();
  }

  const SKELETON_CARDS = 8;

  function showShopSkeleton() {
    if (!shopResults) return;
    const cards = Array.from({ length: SKELETON_CARDS }).map(() =>
      '<div class="skeleton-card">' +
        '<div class="sk-media sk-shimmer"></div>' +
        '<div class="sk-line sk-shimmer"></div>' +
        '<div class="sk-line short sk-shimmer"></div>' +
      '</div>').join('');
    shopResults.innerHTML = '<div class="skeleton-grid">' + cards + '</div>';
  }

  async function loadShop(url) {
    if (!shopResults) return;
    const token = ++shopLoadToken;
    showShopSkeleton();
    try {
      const res = await fetch(url, { headers: { 'X-Requested-With': 'XMLHttpRequest' } });
      const html = await res.text();
      if (token !== shopLoadToken) return;
      shopResults.innerHTML = html;
      syncShopMeta();
      history.pushState({ shop: true }, '', url);
      window.scrollTo({ top: shopResults.offsetTop - 140, behavior: 'smooth' });
    } catch (err) {
      showToast('Could not load results.', 'error');
    }
  }

  function syncShopMeta() {
    const grid = shopResults && shopResults.querySelector('.product-grid');
    const counter = $('#result-count');
    if (grid && counter) {
      const total = grid.dataset.total || '0';
      counter.textContent = total + ' ' + (total === '1' ? 'game' : 'games') + ' found';
      counter.classList.remove('pulse');
      void counter.offsetWidth;
      counter.classList.add('pulse');
    }
    startInfiniteScroll();
  }

  let shopLoadToken = 0;
  let lastShopUrl = filterForm ? shopStateUrl() : location.href;

  if (filterForm && shopResults) {
    filterForm.addEventListener('submit', (e) => { e.preventDefault(); lastShopUrl = shopStateUrl(); loadShop(lastShopUrl); });
  }
  if (sortForm && shopResults) {
    sortForm.addEventListener('change', (e) => {
      if (e.target.name === 'sort_by') { lastShopUrl = shopStateUrl(); loadShop(lastShopUrl); }
    });
  }

  window.addEventListener('popstate', () => {
    if (!shopResults) return;
    const params = new URLSearchParams(location.search);
    if (location.pathname === '/shop/' && (params.toString() || lastShopUrl.includes('/shop/'))) {
      lastShopUrl = location.href;
      loadShop(location.href);
    }
  });

  function startInfiniteScroll() {
    if (!shopResults) return;
    let sentinel = $('#infinite-sentinel', shopResults);
    if (!sentinel) {
      sentinel = document.createElement('div');
      sentinel.id = 'infinite-sentinel';
      sentinel.style.height = '1px';
      shopResults.appendChild(sentinel);
    }
    if (infiniteObserver) infiniteObserver.disconnect();
    let loading = false;
    infiniteObserver = new IntersectionObserver(async (entries) => {
      if (!entries[0].isIntersecting || loading) return;
      const nextLink = shopResults.querySelector('.pagination a.page-next');
      if (!nextLink) return;
      loading = true;
      try {
        const res = await fetch(nextLink.href, { headers: { 'X-Requested-With': 'XMLHttpRequest' } });
        const html = await res.text();
        const tmp = document.createElement('div');
        tmp.innerHTML = html;
        const more = tmp.querySelector('.product-grid');
        const grid = shopResults.querySelector('.product-grid');
        if (grid && more) grid.insertAdjacentHTML('beforeend', more.innerHTML);
        const pagSlot = shopResults.querySelector('#pagination-slot');
        const newPag = tmp.querySelector('.pagination');
        if (pagSlot) pagSlot.innerHTML = newPag ? newPag.outerHTML : '';
        syncShopMeta();
      } catch (err) {
        showToast('Could not load more results.', 'error');
      } finally {
        loading = false;
      }
    }, { rootMargin: '600px' });
    infiniteObserver.observe(sentinel);
  }
  startInfiniteScroll();
  const headerEl = $('.header');
  if (headerEl) {
    let ticking = false;
    const onScroll = () => {
      if (ticking) return;
      ticking = true;
      window.requestAnimationFrame(() => {
        headerEl.classList.toggle('shrunk', window.scrollY > 40);
        ticking = false;
      });
    };
    window.addEventListener('scroll', onScroll, { passive: true });
    onScroll();
  }

  const AJAX_FORMS = ['#filter-form', '#sort-form', 'form[data-newsletter]', '#coupon-form'];
  document.addEventListener('submit', (e) => {
    const form = e.target;
    if (!(form instanceof HTMLFormElement)) return;
    if (form.dataset.noBusy !== undefined) return;
    if (AJAX_FORMS.some((sel) => form.matches(sel))) return;
    const btn = form.querySelector('button[type="submit"], input[type="submit"]');
    if (btn) btn.classList.add('is-busy');
  });

  const checkoutForm = $('#checkout-form');
  if (checkoutForm) {
    checkoutForm.addEventListener('submit', () => {
      const method = checkoutForm.querySelector('input[name="payment_method"]:checked');
      const labels = {
        esewa: 'Opening the eSewa payment screen…',
        khalti: 'Opening the Khalti payment screen…',
        nay_bank: 'Placing order — bank details next…',
        stripe: 'Redirecting to card payment…',
        paypal: 'Redirecting to PayPal…',
      };
      const text = (method && labels[method.value]) || 'Placing your order…';
      showToast(text, 'info');
    });
  }

  document.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-wishlist-toggle], .btn-add-cart, [data-add-to-cart]');
    if (!btn) return;
    btn.classList.remove('is-pressed');
    void btn.offsetWidth;
    btn.classList.add('is-pressed');
    window.setTimeout(() => btn.classList.remove('is-pressed'), 400);
  });
})();
