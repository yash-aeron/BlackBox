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
        '<td><button type="button" class="btn btn-small" data-action="remove" data-id="' + p.id + '" data-focus="remove-' + p.id +
        '" aria-label="Remove ' + esc(p.name) + ' from cart">Remove</button></td></tr>';
    }).join('');

    var discountRow = state.coupon === COUPON
      ? '<div id="discount-row"><span>Discount (SAVE10, 10% off)</span><span class="num" id="cart-discount">-' + fmt(cartDiscount()) + '</span></div>'
      : '<div id="discount-row" hidden><span>Discount</span><span class="num" id="cart-discount">' + fmt(0) + '</span></div>';

    return '<h2>Cart</h2><div class="panel">' +
      '<div class="panel-head"><h3>Items in your cart</h3><p class="hint" id="cart-summary">' + cartCount() + ' item(s)</p></div>' +
      '<div class="table-scroll"><table><caption>Cart contents</caption><thead><tr>' +
      '<th scope="col">Product</th><th scope="col">Category</th><th scope="col">Price</th>' +
      '<th scope="col">Quantity</th><th scope="col">Line total</th><th scope="col">Actions</th>' +
      '</tr></thead><tbody>' + rows + '</tbody></table></div>' +
      '<div class="totals"><div><span>Subtotal</span><span class="num" id="cart-subtotal">' + fmt(cartSubtotal()) + '</span></div>' +
      discountRow +
      '<div class="grand"><span>Total</span><span class="num" id="cart-total">' + fmt(cartTotal()) + '</span></div></div></div>' +
      '<div class="panel"><h3>Coupon code</h3><form id="coupon-form" novalidate>' +
      '<p class="field"><label for="coupon-code">Coupon code</label>' +
      '<input type="text" id="coupon-code" data-focus="coupon-code" value="' + esc(state.coupon || '') + '" autocomplete="off">' +
      '<span class="hint">Promotional codes are applied to the subtotal.</span></p>' +
      '<button type="submit" class="btn" id="apply-coupon" data-focus="apply-coupon">Apply coupon</button></form></div>' +
      '<div class="panel"><div class="btn-row">' +
      '<button type="button" class="btn btn-primary" id="proceed-checkout">Proceed to checkout</button>' +
      '<button type="button" class="btn btn-danger" id="empty-cart">Empty cart</button></div>' +
      '<p class="hint" id="checkout-hint">' + checkoutHint() + '</p></div>';
  }

  function checkoutHint() {
    return state.cart.length ? 'Your cart is ready for checkout.' : 'Add items to your cart to enable checkout.';
  }

  function renderCartNow(focusKey) {
    if (state.route !== 'cart') { return; }
    state.focusKey = focusKey || null;
    viewRoot.innerHTML = cartMarkup();
    wireView();
    applyFocus();
  }

  function updateCartTotalsInPlace() {
    var subtotal = byId('cart-subtotal');
    var total = byId('cart-total');
    var discount = byId('cart-discount');
    var discountRow = byId('discount-row');
    var summary = byId('cart-summary');
    if (subtotal) { subtotal.textContent = fmt(cartSubtotal()); }
    if (total) { total.textContent = fmt(cartTotal()); }
    if (discount) { discount.textContent = '-' + fmt(cartDiscount()); }
    if (discountRow) { discountRow.hidden = state.coupon !== COUPON; }
    if (summary) { summary.textContent = cartCount() + ' item(s)'; }
    state.cart.forEach(function (line) {
      var cell = byId('line-total-' + line.id);
      var p = productById(line.id);
      if (cell && p) { cell.textContent = fmt(p.price * line.qty); }
    });
  }

  function applyCoupon(code) {
    if (String(code || '').trim().toUpperCase() === COUPON) {
      state.coupon = COUPON;
      showStatus('Coupon applied.');
      renderCartNow('apply-coupon');
      return true;
    }
    state.coupon = null;
    showAlert('Invalid coupon code.');
    renderCartNow('coupon-code');
    return false;
  }

  /* Checkout */
  function checkoutMarkup() {
    if (state.orderPlaced) {
      var order = state.orderPlaced;
      return '<h2>Checkout</h2><div class="confirmation" id="order-confirmation"><h3>Order placed</h3>' +
        '<p>Thank you. Your order number is</p><p class="order-number" id="order-number">' + esc(order.number) + '</p>' +
        '<p>Items: ' + order.items + ' \u00b7 Total paid: ' + fmt(order.total) + '</p>' +
        '<p class="muted">A confirmation has been sent to the address on your account.</p>' +
        '<button type="button" class="btn btn-primary" id="continue-shopping">Continue shopping</button></div>';
    }

    return '<h2>Checkout</h2><form id="checkout-form" novalidate>' +
      '<fieldset><legend>Shipping</legend>' +
      '<p class="field"><label for="co-name">Full name</label><input type="text" id="co-name" autocomplete="off"></p>' +
      '<p class="field"><label for="co-address">Address</label><input type="text" id="co-address" autocomplete="off"></p>' +
      '<p class="field"><label for="co-city">City</label><input type="text" id="co-city" autocomplete="off"></p>' +
      '<p class="field"><label for="co-zip">ZIP code</label><input type="text" id="co-zip" inputmode="numeric" autocomplete="off">' +
      '<span class="hint">Five digits, for example 60614.</span></p></fieldset>' +
      '<fieldset><legend>Payment</legend>' +
      '<p class="simulation-note" id="payment-note">Simulation only. No real payment is processed, ' +
      'no card details are transmitted, and nothing is stored after you leave this page.</p>' +
      '<p class="field"><label for="co-card">Card number</label>' +
      '<input type="text" id="co-card" inputmode="numeric" autocomplete="off" placeholder="4242 4242 4242 4242">' +
      '<span class="hint">Sixteen digits, spaces are ignored.</span></p>' +
      '<p class="field"><label for="co-expiry">Expiry (MM/YY)</label>' +
      '<input type="text" id="co-expiry" autocomplete="off" placeholder="09/27"></p>' +
      '<p class="field"><label for="co-cvc">CVC</label><input type="text" id="co-cvc" inputmode="numeric" autocomplete="off" placeholder="123">' +
      '<span class="hint">Three digits from the back of the card.</span></p></fieldset>' +
      '<p class="form-error" id="checkout-error" role="alert" hidden></p>' +
      '<div class="btn-row"><button type="submit" class="btn btn-primary" id="place-order" disabled>Place order</button>' +
      '<button type="button" class="btn" id="back-to-cart">Back to cart</button></div>' +
      '<p class="hint" id="place-order-hint">' + placeOrderHint() + '</p></form>';
  }

  function fieldsComplete() {
    return CHECKOUT_FIELDS.every(function (field) {
      var el = byId(field.id);
      return el && el.value.trim().length > 0;
    });
  }

  function placeOrderHint() {
    if (!state.cart.length) { return 'Add items to your cart before placing an order.'; }
    if (!fieldsComplete()) { return 'Complete every required field to enable Place order.'; }
    return 'All required details are complete. Review and place your order.';
  }

  function syncPlaceOrder() {
    var button = byId('place-order');
    var hint = byId('place-order-hint');
    if (!button) { return; }
    button.disabled = !(state.cart.length > 0 && fieldsComplete());
    if (hint) { hint.textContent = placeOrderHint(); }
  }

  function submitCheckout() {
    var error = byId('checkout-error');
    if (!state.cart.length) {
      if (error) { error.textContent = 'Add items to your cart before placing an order.'; error.hidden = false; }
      return false;
    }
    CHECKOUT_FIELDS.forEach(function (field) {
      var el = byId(field.id);
      if (el) { el.removeAttribute('aria-invalid'); }
    });
    var problem = null;
    CHECKOUT_FIELDS.forEach(function (field) {
      if (problem) { return; }
      var el = byId(field.id);
      if (el && !field.validate(el.value.trim())) { problem = { field: field, el: el }; }
    });
    if (problem) {
      problem.el.setAttribute('aria-invalid', 'true');
      if (error) { error.textContent = problem.field.message; error.hidden = false; }
      problem.el.focus();
      return false;
    }
    if (error) { error.hidden = true; error.textContent = ''; }

    state.orderPlaced = { number: 'MF-' + (2000 + state.orderSeq), total: cartTotal(), items: cartCount() };
    state.orderSeq += 1;
    state.cart = [];
    state.coupon = null;
    updateCartCount();
    showStatus('Order placed.');
    paintView();
    return true;
  }

  /* View wiring */
  function wireView() {
    if (state.route === 'products') {
      var form = byId('filter-form');
      var search = byId('product-search');
      var category = byId('category-filter');
      var sortBy = byId('sort-by');
      if (search) {
        search.addEventListener('input', function () {
          state.query = search.value; refreshProducts(200, 'product-search');
        });
      }
      if (category) {
        category.addEventListener('change', function () {
          state.category = category.value; refreshProducts(200, 'category-filter');
        });
      }
      if (sortBy) {
        sortBy.addEventListener('change', function () {
          state.sortKey = sortBy.value; refreshProducts(200, 'sort-by');
        });
      }
      if (form) { form.addEventListener('submit', function (event) { event.preventDefault(); applyPriceFilters(); }); }
      refreshProducts(300, null);
      return;
    }

    if (state.route === 'cart') {
      var couponForm = byId('coupon-form');
      var proceed = byId('proceed-checkout');
      var browse = byId('browse-products');
      var empty = byId('empty-cart');
      if (couponForm) {
        couponForm.addEventListener('submit', function (event) {
          event.preventDefault(); applyCoupon(byId('coupon-code').value);
        });
      }
      if (proceed) { proceed.addEventListener('click', function () { go('checkout'); }); }
      if (browse) { browse.addEventListener('click', function () { go('products'); }); }
      if (empty) {
        empty.addEventListener('click', function () {
          emptyCartDialog.showModal(); byId('empty-cart-cancel').focus();
        });
      }
      Array.prototype.forEach.call(viewRoot.querySelectorAll('input[type="number"][data-id]'), function (input) {
        input.addEventListener('change', function () { onQuantityChange(input); });
      });
      return;
    }

    var checkoutForm = byId('checkout-form');
    if (checkoutForm) {
      CHECKOUT_FIELDS.forEach(function (field) {
        var el = byId(field.id);
        if (el) { el.addEventListener('input', syncPlaceOrder); }
      });
      checkoutForm.addEventListener('submit', function (event) { event.preventDefault(); submitCheckout(); });
      var back = byId('back-to-cart');
      if (back) { back.addEventListener('click', function () { go('cart'); }); }
      syncPlaceOrder();
    }
    var continueShopping = byId('continue-shopping');
    if (continueShopping) {
      continueShopping.addEventListener('click', function () {
        state.orderPlaced = null;
        go('products');
      });
    }
  }

  function applyPriceFilters() {
    var minField = byId('min-price');
    var maxField = byId('max-price');
    var error = byId('price-error');
    var minRaw = minField.value.trim();
    var maxRaw = maxField.value.trim();
    var min = minRaw === '' ? null : Number(minRaw);
    var max = maxRaw === '' ? null : Number(maxRaw);
    var negative = (min !== null && !(min >= 0)) || (max !== null && !(max >= 0));
    var inverted = min !== null && max !== null && min > max;
    minField.removeAttribute('aria-invalid');
    maxField.removeAttribute('aria-invalid');
    if (negative || inverted) {
      var message = negative ? 'Prices cannot be negative.' : 'Minimum price cannot be greater than maximum price.';
      if (negative) {
        if (min !== null && !(min >= 0)) { minField.setAttribute('aria-invalid', 'true'); }
        if (max !== null && !(max >= 0)) { maxField.setAttribute('aria-invalid', 'true'); }
      } else {
        minField.setAttribute('aria-invalid', 'true');
        maxField.setAttribute('aria-invalid', 'true');
      }
      error.textContent = message;
      error.hidden = false;
      return false;
    }
    error.hidden = true;
    error.textContent = '';
    state.minPrice = min;
    state.maxPrice = max;
    showStatus('Filters applied.');
    refreshProducts(300, 'apply-filters');
    return true;
  }

  function onQuantityChange(input) {
    var line = cartLine(input.getAttribute('data-id'));
    if (!line) { return; }
    var qty = Math.floor(Number(input.value));
    if (!isFinite(qty) || qty < 1) { qty = 1; }
    if (qty > MAX_QTY) {
      qty = MAX_QTY;
      showAlert('Quantity limited to ' + MAX_QTY + ' per item.');
    }
    input.value = String(qty);
    line.qty = qty;
    updateCartCount();
    updateCartTotalsInPlace();
  }

  function onViewClick(event) {
    var actionButton = event.target.closest('[data-action]');
    if (!actionButton) { return; }
    var action = actionButton.getAttribute('data-action');
    var id = actionButton.getAttribute('data-id');

    if (action === 'details') {
      state.openerFocusKey = 'details-' + id;
      openProductDialog(id);
      return;
    }
    if (action === 'add') {
      if (actionButton.disabled) { return; }
      addToCart(id);
      return;
    }
    if (action === 'remove') {
      var p = productById(id);
      state.cart = state.cart.filter(function (l) { return l.id !== id; });
      if (!state.cart.length) { state.coupon = null; }
      updateCartCount();
      if (p) { showStatus('Removed ' + p.name + ' from cart.'); }
      var nextLine = state.cart.length ? state.cart[0] : null;
      renderCartNow(nextLine ? 'remove-' + nextLine.id : 'browse-products');
    }
  }

  /* Bootstrap */
  window.addEventListener('hashchange', function () { clearBanners(); render(); });
  document.addEventListener('click', onViewClick);

  productDialog.addEventListener('click', function (event) {
    if (event.target.closest('#dialog-close')) { productDialog.close(); return; }
    var add = event.target.closest('#dialog-add');
    if (add && !add.disabled) {
      addToCart(add.getAttribute('data-id'));
      productDialog.close();
    }
  });

  productDialog.addEventListener('close', function () {
    var key = state.openerFocusKey;
    state.openerFocusKey = null;
    if (!key) { return; }
    var target = viewRoot.querySelector('[data-focus="' + key + '"]');
    if (target && typeof target.focus === 'function') { target.focus(); }
  });

  byId('empty-cart-cancel').addEventListener('click', function () { emptyCartDialog.close(); });

  byId('empty-cart-confirm').addEventListener('click', function () {
    state.cart = [];
    state.coupon = null;
    updateCartCount();
    emptyCartDialog.close();
    showStatus('Cart emptied.');
    renderCartNow('browse-products');
  });

  if (!location.hash || location.hash === '#/' || ROUTES.indexOf(parseHash().path) === -1) {
    history.replaceState(null, '', '#/products');
  }
  updateCartCount();
  render();
})();
