/* Marlowe & Finch - storefront application */
(function () {
  'use strict';

  var PRODUCTS = [
    { id: 'p1',  name: 'The Quiet Harbour',         category: 'Books',       price: 18.50, rating: 4.4, inStock: true,  description: 'A slow-burning novel about a lighthouse keeper and the letters he never sends. 384 pages, paperback.' },
    { id: 'p2',  name: 'Atlas of Small Inventions', category: 'Books',       price: 32.00, rating: 4.1, inStock: true,  description: 'Illustrated histories of forty everyday objects, from the paperclip to the zip fastener.' },
    { id: 'p3',  name: 'Everyday Bread Baking',     category: 'Books',       price: 24.75, rating: 4.6, inStock: false, description: 'Sixty no-knead recipes with a chapter on sourdough starters for small kitchens.' },
    { id: 'p4',  name: 'Lumen Wireless Headphones', category: 'Electronics', price: 129.00, rating: 4.7, inStock: true,  description: 'Over-ear headphones with 38 hours of playback and a replaceable battery.' },
    { id: 'p5',  name: 'Pocket Power Bank 10000',   category: 'Electronics', price: 39.99, rating: 4.2, inStock: true,  description: 'Pocket-sized 10,000 mAh battery with two USB-C ports and charge indicators.' },
    { id: 'p6',  name: 'Nimbus Bluetooth Speaker',  category: 'Electronics', price: 74.50, rating: 3.9, inStock: false, description: 'Water-resistant portable speaker with a woven carry strap. Currently awaiting stock.' },
    { id: 'p7',  name: 'Brass Desk Lamp',           category: 'Home',        price: 58.00, rating: 4.5, inStock: true,  description: 'Weighted brass lamp with an adjustable arm and a warm dimmable bulb included.' },
    { id: 'p8',  name: 'Stoneware Mug Set of 4',    category: 'Home',        price: 42.00, rating: 4.8, inStock: true,  description: 'Hand-glazed stoneware mugs, dishwasher safe, each holding 350 ml.' },
    { id: 'p9',  name: 'Linen Throw Blanket',       category: 'Home',        price: 89.00, rating: 4.3, inStock: false, description: 'Stonewashed linen throw in oatmeal, 130 by 170 cm. Restocking next month.' },
    { id: 'p10', name: 'Cedar Cutting Board',       category: 'Home',        price: 34.25, rating: 4.6, inStock: true,  description: 'End-grain cedar board with a juice groove and an oiled finish.' },
    { id: 'p11', name: 'Wooden Railway Starter Set', category: 'Toys',       price: 64.00, rating: 4.9, inStock: true,  description: 'Forty-piece beech railway with two bridges, a station and a wooden train.' },
    { id: 'p12', name: 'Puzzle Globe 500 Pieces',   category: 'Toys',        price: 27.50, rating: 4.0, inStock: true,  description: 'A 500-piece jigsaw that builds into a 20 cm globe on the included stand.' },
    { id: 'p13', name: 'Magnetic Building Tiles 60', category: 'Toys',       price: 49.99, rating: 4.4, inStock: false, description: 'Sixty magnetic tiles in four shapes for open-ended building. Out of stock.' },
    { id: 'p14', name: 'Plush Fox Hand Puppet',     category: 'Toys',        price: 16.75, rating: 4.2, inStock: true,  description: 'Soft-toy fox puppet with a movable mouth, suitable from age three.' }
  ];

  var CATEGORIES = ['All', 'Books', 'Electronics', 'Home', 'Toys'];
  var ROUTES = ['products', 'cart', 'checkout'];
  var VIEW_DELAY_MS = { products: 320, cart: 200, checkout: 250 };
  var MAX_QTY = 10;
  var COUPON = 'SAVE10';

  var SORTERS = {
    'name-asc': function (a, b) { return a.name.localeCompare(b.name); },
    'name-desc': function (a, b) { return b.name.localeCompare(a.name); },
    'price-asc': function (a, b) { return a.price - b.price; },
    'price-desc': function (a, b) { return b.price - a.price; },
    'rating-desc': function (a, b) { return b.rating - a.rating || a.name.localeCompare(b.name); }
  };

  var SORT_LABELS = [
    ['name-asc', 'Name (A to Z)'], ['name-desc', 'Name (Z to A)'], ['price-asc', 'Price (low to high)'],
    ['price-desc', 'Price (high to low)'], ['rating-desc', 'Rating (high to low)']
  ];

  var CHECKOUT_FIELDS = [
    { id: 'co-name',    message: 'Enter your full name.',      validate: function (v) { return v.length > 0; } },
    { id: 'co-address', message: 'Enter your street address.', validate: function (v) { return v.length > 0; } },
    { id: 'co-city',    message: 'Enter your city.',           validate: function (v) { return v.length > 0; } },
    { id: 'co-zip',     message: 'Enter a 5 digit ZIP code.',  validate: function (v) { return isMatch(v, /^\d{5}$/); } },
    { id: 'co-card',    message: 'Enter a 16 digit card number.',
      validate: function (v) { return isMatch(v.replace(/\s+/g, ''), /^\d{16}$/); } },
    { id: 'co-expiry',  message: 'Enter the expiry date as MM/YY.',
      validate: function (v) { return isMatch(v, /^(0[1-9]|1[0-2])\/\d{2}$/); } },
    { id: 'co-cvc',     message: 'Enter the 3 digit CVC.',     validate: function (v) { return isMatch(v, /^\d{3}$/); } }
  ];

  var state = {
    route: 'products', params: {},
    products: PRODUCTS.map(function (p) { return Object.assign({}, p); }),
    query: '', category: 'All', minPrice: null, maxPrice: null, sortKey: 'name-asc',
    cart: [], coupon: null, orderSeq: 1, orderPlaced: null,
    focusKey: null, openerFocusKey: null, dialogProductId: null
  };

  var viewRoot = document.getElementById('view-root');
  var statusBanner = document.getElementById('status-banner');
  var alertBanner = document.getElementById('alert-banner');
  var navCount = document.getElementById('nav-cart-count');
  var productDialog = document.getElementById('product-dialog');
  var productDialogBody = document.getElementById('product-dialog-body');
  var emptyCartDialog = document.getElementById('empty-cart-dialog');
  var renderToken = 0;
  var resultsToken = 0;

  function esc(value) {
    return String(value === null || value === undefined ? '' : value)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
  function isMatch(value, pattern) { return value.match(pattern) !== null; }
  var money = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: 2 });
  function fmt(value) { return money.format(Math.round((Number(value) || 0) * 100) / 100); }
  function byId(id) { return document.getElementById(id); }
  function productById(id) { return state.products.filter(function (p) { return p.id === id; })[0] || null; }
  function showStatus(message) { alertBanner.hidden = true; statusBanner.textContent = message; statusBanner.hidden = false; }
  function showAlert(message) { statusBanner.hidden = true; alertBanner.textContent = message; alertBanner.hidden = false; }
  function clearBanners() {
    statusBanner.hidden = true; alertBanner.hidden = true;
    statusBanner.textContent = ''; alertBanner.textContent = '';
  }

  /* Routing: the hash drives the view, filters and cart live in memory. */
  function parseHash() {
    var raw = String(location.hash || '').replace(/^#\/?/, '');
    var split = raw.split('?');
    var path = split[0] || 'products';
    var params = {};
    if (split[1]) {
      split[1].split('&').forEach(function (pair) {
        var bits = pair.split('=');
        if (bits[0]) { params[decodeURIComponent(bits[0])] = decodeURIComponent(bits[1] || ''); }
      });
    }
    if (ROUTES.indexOf(path) === -1) { path = 'products'; }
    return { path: path, params: params };
  }

  function go(route) {
    if (location.hash === '#/' + route) { render(); } else { location.hash = '#/' + route; }
  }

  function setActiveNav(route) {
    Array.prototype.forEach.call(document.querySelectorAll('.site-nav a[data-route]'), function (link) {
      if (link.getAttribute('data-route') === route) { link.setAttribute('aria-current', 'page'); }
      else { link.removeAttribute('aria-current'); }
    });
  }

  function render() {
    var parsed = parseHash();
    state.route = parsed.path;
    state.params = parsed.params;
    setActiveNav(state.route);
    var token = ++renderToken;
    viewRoot.innerHTML = '<p class="loading" role="status">Loading\u2026</p>';
    window.setTimeout(function () {
      if (token !== renderToken) { return; }
      paintView();
    }, VIEW_DELAY_MS[state.route] || 250);
  }

  function paintView() {
    if (state.route === 'products') { viewRoot.innerHTML = productsMarkup(); }
    else if (state.route === 'cart') { viewRoot.innerHTML = cartMarkup(); }
    else { viewRoot.innerHTML = checkoutMarkup(); }
    wireView();
    applyFocus();
  }

  function applyFocus() {
    if (!state.focusKey) { return; }
    var target = viewRoot.querySelector('[data-focus="' + state.focusKey + '"]');
    state.focusKey = null;
    if (target && typeof target.focus === 'function' && !target.disabled) { target.focus(); }
  }

  /* Products */
  function productsMarkup() {
    var categoryOptions = CATEGORIES.map(function (c) {
      return '<option value="' + esc(c) + '"' + (state.category === c ? ' selected' : '') + '>' + esc(c) + '</option>';
    }).join('');
    var sortOptions = SORT_LABELS.map(function (pair) {
      return '<option value="' + pair[0] + '"' + (state.sortKey === pair[0] ? ' selected' : '') + '>' + pair[1] + '</option>';
    }).join('');
    return '<h2>Products</h2><div class="panel"><h3>Filter the catalogue</h3>' +
      '<form id="filter-form" novalidate><div class="filters">' +
      '<p class="field"><label for="product-search">Search products</label>' +
      '<input type="search" id="product-search" data-focus="product-search" value="' + esc(state.query) + '" placeholder="Name or description"></p>' +
      '<p class="field"><label for="category-filter">Category</label>' +
      '<select id="category-filter" data-focus="category-filter">' + categoryOptions + '</select></p>' +
      '<p class="field"><label for="min-price">Min price</label>' +
      '<input type="number" id="min-price" min="0" step="0.01" value="' + (state.minPrice === null ? '' : state.minPrice) + '"></p>' +
      '<p class="field"><label for="max-price">Max price</label>' +
      '<input type="number" id="max-price" min="0" step="0.01" value="' + (state.maxPrice === null ? '' : state.maxPrice) + '"></p>' +
      '<p class="field"><label for="sort-by">Sort by</label>' +
      '<select id="sort-by" data-focus="sort-by">' + sortOptions + '</select></p>' +
      '<p class="field"><button type="submit" class="btn btn-primary" id="apply-filters" data-focus="apply-filters">Apply filters</button></p>' +
      '</div><p class="hint">Price limits apply when you select Apply filters. Search and category update as you change them.</p>' +
      '<p class="form-error" id="price-error" role="alert" hidden></p></form></div>' +
      '<div id="product-results" class="product-results" role="region" aria-label="Product results"></div>';
  }

  function visibleProducts() {
    var q = state.query.trim().toLowerCase();
    var min = state.minPrice;
    var max = state.maxPrice;
    return state.products.filter(function (p) {
      if (state.category !== 'All' && p.category !== state.category) { return false; }
      if (q && (p.name + ' ' + p.category + ' ' + p.description).toLowerCase().indexOf(q) === -1) { return false; }
      if (min !== null && p.price < min) { return false; }
      if (max !== null && p.price > max) { return false; }
      return true;
    }).sort(SORTERS[state.sortKey] || SORTERS['name-asc']);
  }

  function productCardMarkup(p) {
    var badge = '<span class="badge ' + (p.inStock ? 'badge-in' : 'badge-out') + '" id="stock-' + p.id + '">' +
      (p.inStock ? 'In stock' : 'Out of stock') + '</span>';
    var addButton = p.inStock
      ? '<button type="button" class="btn btn-primary" data-action="add" data-id="' + p.id + '" data-focus="add-' + p.id + '" aria-label="Add to cart: ' + esc(p.name) + '">Add to cart</button>'
      : '<button type="button" class="btn" data-action="add" data-id="' + p.id + '" disabled title="Out of stock" aria-label="Add to cart: ' + esc(p.name) + ' (out of stock)" aria-describedby="stock-' + p.id + '">Add to cart</button>';
    return '<li class="product-card"><h3>' + esc(p.name) + '</h3><p class="product-meta">' + esc(p.category) + '</p>' +
      '<p class="product-price">' + fmt(p.price) + '</p><p class="product-rating">Rating ' + p.rating.toFixed(1) + ' out of 5</p>' +
      '<p>' + badge + '</p><div class="product-actions">' +
      '<button type="button" class="btn" data-action="details" data-id="' + p.id + '" data-focus="details-' + p.id + '" aria-label="View details: ' + esc(p.name) + '">View details</button>' +
      addButton + '</div></li>';
  }

  function productResultsMarkup() {
    var rows = visibleProducts();
    var label = rows.length === 1 ? '1 product' : rows.length + ' products';
    var grid = rows.length ? '<ul class="product-grid">' + rows.map(productCardMarkup).join('') + '</ul>'
      : '<p class="muted">No products match these filters.</p>';
    return '<p class="hint" id="product-count">Showing ' + label + '</p>' + grid;
  }

  function refreshProducts(delay, focusKey) {
    var region = byId('product-results');
    if (!region) { return; }
    var token = ++resultsToken;
    state.focusKey = focusKey || null;
    region.innerHTML = '<p class="loading" role="status">Loading\u2026</p>';
    window.setTimeout(function () {
      if (token !== resultsToken) { return; }
      var live = byId('product-results');
      if (!live) { return; }
      live.innerHTML = productResultsMarkup();
      applyFocus();
    }, delay === undefined ? 200 : delay);
  }

  /* Product detail dialog */
  function openProductDialog(id) {
    var p = productById(id);
    if (!p) { return; }
    state.dialogProductId = id;
    var addButton = p.inStock
      ? '<button type="button" class="btn btn-primary" id="dialog-add" data-id="' + p.id + '">Add to cart</button>'
      : '<button type="button" class="btn" id="dialog-add" data-id="' + p.id + '" disabled title="Out of stock" aria-describedby="dialog-stock">Add to cart</button>';
    productDialogBody.innerHTML = '<h2 id="product-dialog-title">' + esc(p.name) + '</h2>' +
      '<p class="product-meta">' + esc(p.category) + '</p><p class="product-price">' + fmt(p.price) + '</p>' +
      '<p>Rating ' + p.rating.toFixed(1) + ' out of 5</p>' +
      '<p><span class="badge ' + (p.inStock ? 'badge-in' : 'badge-out') + '" id="dialog-stock">' + (p.inStock ? 'In stock' : 'Out of stock') + '</span></p>' +
      '<p>' + esc(p.description) + '</p><div class="dialog-actions">' +
      '<button type="button" class="btn btn-ghost" id="dialog-close">Close</button>' + addButton + '</div>';
    productDialog.showModal();
    byId('dialog-close').focus();
  }

  /* Cart state */
  function cartLine(id) { return state.cart.filter(function (l) { return l.id === id; })[0] || null; }
  function cartCount() { return state.cart.reduce(function (sum, l) { return sum + l.qty; }, 0); }
  function cartSubtotal() {
    return state.cart.reduce(function (sum, l) {
      var p = productById(l.id);
      return sum + (p ? p.price * l.qty : 0);
    }, 0);
  }
  function cartDiscount() { return state.coupon === COUPON ? cartSubtotal() * 0.1 : 0; }
  function cartTotal() { return cartSubtotal() - cartDiscount(); }
  function updateCartCount() { navCount.textContent = String(cartCount()); }

  function addToCart(id) {
    var p = productById(id);
    if (!p) { return; }
    if (!p.inStock) { showAlert('That item is out of stock.'); return; }
    var line = cartLine(id);
    if (line) {
      if (line.qty >= MAX_QTY) { showAlert('Quantity limited to ' + MAX_QTY + ' per item.'); return; }
      line.qty += 1;
    } else {
      state.cart.push({ id: id, qty: 1 });
    }
    updateCartCount();
    showStatus('Added to cart: ' + p.name + '.');
    if (state.route === 'cart') { renderCartNow('remove-' + id); }
  }

  /* Cart view */
  function cartMarkup() {
    if (!state.cart.length) {
      return '<h2>Cart</h2><div class="panel"><h3>Your cart is empty</h3>' +
        '<p class="muted">Add a few items from the catalogue to see them here.</p>' +
        '<button type="button" class="btn btn-primary" id="browse-products" data-focus="browse-products">Browse products</button></div>';
    }

    var rows = state.cart.map(function (line) {
      var p = productById(line.id);
      if (!p) { return ''; }
      return '<tr><th scope="row">' + esc(p.name) + '</th><td>' + esc(p.category) + '</td>' +
        '<td class="num">' + fmt(p.price) + '</td>' +
        '<td><label class="hint" for="qty-' + p.id + '">Quantity for ' + esc(p.name) + '</label>' +
        '<input type="number" id="qty-' + p.id + '" data-id="' + p.id + '" min="1" max="' + MAX_QTY + '" step="1" value="' + line.qty +
        '" aria-label="Quantity for ' + esc(p.name) + '"></td>' +
        '<td class="num" id="line-total-' + p.id + '">' + fmt(p.price * line.qty) + '</td>' +
