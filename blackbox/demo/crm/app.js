/* Northwind CRM - single page application */
(function () {
  'use strict';

  var SEED_CUSTOMERS = [
    { id: 'c1',  name: 'Amara Okafor',       email: 'amara.okafor@brightlaneco.com',    company: 'Bright Lane Co',        status: 'Active',   revenue: 48250, createdAt: '2023-01-14' },
    { id: 'c2',  name: 'Daniel Whitfield',   email: 'd.whitfield@harborline.io',        company: 'Harborline Logistics',  status: 'Inactive', revenue: 12400, createdAt: '2023-02-02' },
    { id: 'c3',  name: 'Priya Raghunathan',  email: 'priya@meridianlabs.dev',           company: 'Meridian Labs',         status: 'Active',   revenue: 91300, createdAt: '2023-02-27' },
    { id: 'c4',  name: 'Tomas Bergstrom',    email: 'tomas.bergstrom@nordvik.se',       company: 'Nordvik Trading',       status: 'Lead',     revenue: 0,     createdAt: '2023-03-11' },
    { id: 'c5',  name: 'Grace Lindqvist',    email: 'grace@fernwoodstudio.com',         company: 'Fernwood Studio',       status: 'Active',   revenue: 22750, createdAt: '2023-04-05' },
    { id: 'c6',  name: 'Marcus Adeyemi',     email: 'marcus.adeyemi@quartzpoint.com',   company: 'Quartz Point',          status: 'Lead',     revenue: 0,     createdAt: '2023-04-22' },
    { id: 'c7',  name: 'Elena Marchetti',    email: 'elena@vinetoworks.it',             company: 'Vineto Works',          status: 'Active',   revenue: 64800, createdAt: '2023-05-09' },
    { id: 'c8',  name: 'Samuel Njoroge',     email: 's.njoroge@savannahgrid.co.ke',     company: 'Savannah Grid',         status: 'Inactive', revenue: 8600,  createdAt: '2023-06-18' },
    { id: 'c9',  name: 'Hannah Petrov',      email: 'hannah.petrov@lumenfield.com',     company: 'Lumen Field Analytics', status: 'Active',   revenue: 134900, createdAt: '2023-07-03' },
    { id: 'c10', name: 'Yusuf Demir',        email: 'yusuf@anatoliaworks.tr',           company: 'Anatolia Works',        status: 'Lead',     revenue: 0,     createdAt: '2023-07-29' },
    { id: 'c11', name: 'Clara Bennett',      email: 'clara.bennett@oakridgedesign.com', company: 'Oak Ridge Design',      status: 'Active',   revenue: 31200, createdAt: '2023-08-15' },
    { id: 'c12', name: 'Ravi Chandrasekhar', email: 'ravi@deltawave.in',                company: 'Delta Wave Systems',    status: 'Inactive', revenue: 19950, createdAt: '2023-09-07' },
    { id: 'c13', name: 'Nadia Haddad',       email: 'nadia.haddad@cedarline.ae',        company: 'Cedar Line Interiors',  status: 'Active',   revenue: 57300, createdAt: '2023-10-21' },
    { id: 'c14', name: 'Owen Fitzgerald',    email: 'owen@stonebridgelegal.com',        company: 'Stonebridge Legal',     status: 'Lead',     revenue: 0,     createdAt: '2023-11-12' },
    { id: 'c15', name: 'Mei Lin Tan',        email: 'mei.tan@orchidbay.sg',             company: 'Orchid Bay Trading',    status: 'Active',   revenue: 74200, createdAt: '2024-01-08' }
  ];

  var ROUTES = ['dashboard', 'customers', 'reports', 'settings'];
  var VIEW_DELAY_MS = { dashboard: 280, customers: 340, reports: 220, settings: 180 };
  var SORTABLE = { name: 'Name', revenue: 'Revenue' };

  var state = {
    route: 'dashboard', params: {},
    customers: SEED_CUSTOMERS.map(function (c) { return Object.assign({}, c); }),
    nextId: 100, query: '', statusFilter: 'all', pageSize: 5, page: 1,
    sortKey: 'name', sortDir: 'asc', editingId: null, pendingDeleteId: null, highlightId: null,
    reportFrom: '', reportTo: '', reportRows: null,
    settings: { emailNotifications: true, defaultPageSize: 5 },
    focusKey: null, openerFocusKey: null, lastTableDelay: 200
  };

  var viewRoot = document.getElementById('view-root');
  var statusBanner = document.getElementById('status-banner');
  var alertBanner = document.getElementById('alert-banner');
  var customerDialog = document.getElementById('customer-dialog');
  var customerForm = document.getElementById('customer-form');
  var confirmDialog = document.getElementById('confirm-dialog');
  var renderToken = 0;
  var tableToken = 0;

  function esc(value) {
    return String(value === null || value === undefined ? '' : value)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
  var money = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 });
  function fmtMoney(value) { return money.format(Number(value) || 0); }
  function byId(id) { return document.getElementById(id); }
  function showStatus(message) { alertBanner.hidden = true; statusBanner.textContent = message; statusBanner.hidden = false; }
  function showAlert(message) { statusBanner.hidden = true; alertBanner.textContent = message; alertBanner.hidden = false; }
  function clearBanners() {
    statusBanner.hidden = true; alertBanner.hidden = true;
    statusBanner.textContent = ''; alertBanner.textContent = '';
  }

  /* Routing: the hash drives the view, filters and paging stay in memory. */
  function parseHash() {
    var raw = String(location.hash || '').replace(/^#\/?/, '');
    var split = raw.split('?');
    var path = split[0] || 'dashboard';
    var params = {};
    if (split[1]) {
      split[1].split('&').forEach(function (pair) {
        var bits = pair.split('=');
        if (bits[0]) { params[decodeURIComponent(bits[0])] = decodeURIComponent(bits[1] || ''); }
      });
    }
    if (ROUTES.indexOf(path) === -1) { path = 'dashboard'; }
    return { path: path, params: params };
  }

  function go(route, params) {
    var target = '#/' + route;
    if (params) {
      var pairs = Object.keys(params).map(function (k) {
        return encodeURIComponent(k) + '=' + encodeURIComponent(params[k]);
      });
      if (pairs.length) { target += '?' + pairs.join('&'); }
    }
    if (location.hash === target) { render(); } else { location.hash = target; }
  }

  function setActiveNav(route) {
    Array.prototype.forEach.call(document.querySelectorAll('.app-nav a[data-route]'), function (link) {
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
    if (state.route === 'dashboard') { viewRoot.innerHTML = dashboardMarkup(); }
    else if (state.route === 'customers') { viewRoot.innerHTML = customersMarkup(); renderTable(true); }
    else if (state.route === 'reports') { viewRoot.innerHTML = reportsMarkup(); }
    else { viewRoot.innerHTML = settingsMarkup(); }
    wireView();
    applyFocus();
    if (state.route === 'reports' && state.params.export === '1') { attemptExport(); }
  }

  function applyFocus() {
    if (!state.focusKey) { return; }
    var target = viewRoot.querySelector('[data-focus="' + state.focusKey + '"]');
    state.focusKey = null;
    if (target && typeof target.focus === 'function' && !target.disabled) { target.focus(); }
  }

  function restoreOpenerFocus() {
    var key = state.openerFocusKey;
    state.openerFocusKey = null;
    if (!key) { return; }
    var target = document.querySelector('[data-focus="' + key + '"]');
    if (target && typeof target.focus === 'function') { target.focus(); }
  }

  /* Dashboard */
  function dashboardMarkup() {
    var total = state.customers.length;
    var active = state.customers.filter(function (c) { return c.status === 'Active'; }).length;
    var leads = state.customers.filter(function (c) { return c.status === 'Lead'; }).length;
    var revenue = state.customers.reduce(function (sum, c) { return sum + (Number(c.revenue) || 0); }, 0);
    var stats = [['Total customers', total], ['Active customers', active], ['Open leads', leads], ['Total revenue', fmtMoney(revenue)]];
    return '<h2>Dashboard</h2>' +
      '<p class="muted">A snapshot of the customer book. Figures update as records change.</p>' +
      '<ul class="stat-grid">' + stats.map(function (s) {
        return '<li class="stat"><span class="stat-label">' + s[0] + '</span><span class="stat-value">' + s[1] + '</span></li>';
      }).join('') + '</ul>' +
      '<div class="panel"><h3>Quick actions</h3>' +
      '<p class="muted">Jump to the full customer list or create a new record.</p>' +
      '<div class="btn-row">' +
      '<button type="button" class="btn btn-primary" data-focus="dashboard-customers" id="dashboard-customers">Customers</button>' +
      '<button type="button" class="btn" data-focus="dashboard-add" id="dashboard-add">Add customer</button>' +
      '</div></div>';
  }

  /* Customers */
  function customersMarkup() {
    var statusOptions = ['all', 'Active', 'Inactive', 'Lead'].map(function (value) {
      var text = value === 'all' ? 'All' : value;
      return '<option value="' + value + '">' + text + '</option>';
    }).join('');
    var sizeOptions = [5, 10, 25].map(function (n) {
      return '<option value="' + n + '">' + n + '</option>';
    }).join('');
    return '<h2>Customers</h2><div class="panel">' +
      '<div class="panel-head"><h3>Customer list</h3>' +
      '<button type="button" class="btn btn-primary" data-focus="add-customer" id="add-customer">Add customer</button></div>' +
      '<div class="filters">' +
      '<p class="field"><label for="customer-search">Search customers</label>' +
      '<input type="search" id="customer-search" data-focus="customer-search" value="' + esc(state.query) + '" placeholder="Name, email or company"></p>' +
      '<p class="field"><label for="status-filter">Status</label>' +
      '<select id="status-filter" data-focus="status-filter">' + statusOptions + '</select></p>' +
      '<p class="field"><label for="page-size">Rows per page</label>' +
      '<select id="page-size" data-focus="page-size">' + sizeOptions + '</select></p>' +
      '</div><div id="customers-table-region"></div></div>';
  }

  function filteredCustomers() {
    var q = state.query.trim().toLowerCase();
    var rows = state.customers.filter(function (c) {
      if (state.statusFilter !== 'all' && c.status !== state.statusFilter) { return false; }
      if (!q) { return true; }
      return (c.name + ' ' + c.email + ' ' + c.company).toLowerCase().indexOf(q) !== -1;
    });
    var dir = state.sortDir === 'asc' ? 1 : -1;
    rows.sort(function (a, b) {
      if (state.sortKey === 'revenue') { return ((Number(a.revenue) || 0) - (Number(b.revenue) || 0)) * dir; }
      return a.name.localeCompare(b.name) * dir;
    });
    return rows;
  }

  function pageInfo(rows) {
    var totalPages = Math.max(1, Math.ceil(rows.length / state.pageSize));
    state.page = Math.min(Math.max(1, state.page), totalPages);
    var start = (state.page - 1) * state.pageSize;
    return { totalPages: totalPages, start: start, pageRows: rows.slice(start, start + state.pageSize) };
  }

  function sortableHeader(key, label) {
    var ariaSort = state.sortKey === key ? (state.sortDir === 'asc' ? 'ascending' : 'descending') : 'none';
    return '<th scope="col" aria-sort="' + ariaSort + '">' +
      '<button type="button" class="sort" data-sort="' + key + '">' + esc(label) + '</button></th>';
  }

  function tableMarkup() {
    var rows = filteredCustomers();
    var info = pageInfo(rows);
    var first = rows.length === 0 ? 0 : info.start + 1;
    var last = info.start + info.pageRows.length;
    var body = info.pageRows.map(function (c) {
      var rowClass = c.id === state.highlightId ? ' class="is-new"' : '';
      return '<tr' + rowClass + '><td>' + esc(c.name) + '</td><td>' + esc(c.email) + '</td><td>' + esc(c.company) + '</td>' +
        '<td><span class="pill pill-' + c.status.toLowerCase() + '">' + esc(c.status) + '</span></td>' +
        '<td class="num">' + fmtMoney(c.revenue) + '</td><td>' +
        '<button type="button" class="btn btn-small" data-action="edit" data-id="' + c.id + '" data-focus="edit-' + c.id + '" aria-label="Edit ' + esc(c.name) + '">Edit</button> ' +
        '<button type="button" class="btn btn-small" data-action="delete" data-id="' + c.id + '" data-focus="delete-' + c.id + '" aria-label="Delete ' + esc(c.name) + '">Delete</button>' +
        '</td></tr>';
    }).join('');
    if (!body) { body = '<tr><td colspan="6">No customers match these filters.</td></tr>'; }
    return '<div class="table-scroll"><table><caption>Customer records</caption><thead><tr>' +
      sortableHeader('name', 'Name') + '<th scope="col">Email</th><th scope="col">Company</th><th scope="col">Status</th>' +
      sortableHeader('revenue', 'Revenue') + '<th scope="col">Actions</th></tr></thead><tbody>' + body + '</tbody></table></div>' +
      '<div class="table-footer"><div>' +
      '<p class="page-indicator" id="page-indicator" tabindex="-1" data-focus="page-indicator">Page ' + state.page + ' of ' + info.totalPages + '</p>' +
      '<p class="hint" id="rows-summary">Showing ' + first + '\u2013' + last + ' of ' + rows.length + ' customers</p></div>' +
      '<div class="pager">' +
      '<button type="button" class="btn" id="prev-page" data-focus="prev-page"' + (state.page <= 1 ? ' disabled' : '') + '>Previous</button>' +
      '<button type="button" class="btn" id="next-page" data-focus="next-page"' + (state.page >= info.totalPages ? ' disabled' : '') + '>Next</button>' +
      '</div></div>';
  }

  function renderTable(instant) {
    var region = byId('customers-table-region');
    if (!region) { return; }
    var token = ++tableToken;
    if (instant) { region.innerHTML = tableMarkup(); applyFocus(); return; }
    region.innerHTML = '<p class="loading" role="status">Loading\u2026</p>';
    window.setTimeout(function () {
      if (token !== tableToken) { return; }
      var live = byId('customers-table-region');
      if (!live) { return; }
      live.innerHTML = tableMarkup();
      applyFocus();
    }, state.lastTableDelay);
  }

  function refreshCustomers(delay, focusKey) {
    state.lastTableDelay = delay;
    state.focusKey = focusKey || null;
    renderTable(false);
  }

  /* Reports */
  function reportsMarkup() {
    var both = Boolean(state.reportFrom && state.reportTo);
    var hint = both ? 'Date range ready. Select Export report to run it.' : 'Select both From and To dates to enable export.';
    var title = both ? 'Export the selected date range' : 'Select both From and To dates to enable export';
    return '<h2>Reports</h2><div class="panel"><h3>Export customer activity</h3>' +
      '<p class="muted">Choose the period to include. New customer records are dated on creation.</p>' +
      '<form id="report-form" novalidate><div class="filters">' +
      '<p class="field"><label for="report-from">From</label><input type="date" id="report-from" value="' + esc(state.reportFrom) + '"></p>' +
      '<p class="field"><label for="report-to">To</label><input type="date" id="report-to" value="' + esc(state.reportTo) + '"></p>' +
      '<p class="field"><button type="submit" class="btn btn-primary" id="export-report"' + (both ? '' : ' disabled') +
      ' title="' + title + '">Export report</button></p>' +
      '</div><p class="hint" id="export-hint">' + hint + '</p>' +
