'use strict';

/* Static seed data. All values are literal; nothing is generated. */

const TASK_STATUSES = ['Not started', 'In progress', 'In review', 'Blocked', 'Completed'];
const PRIORITIES = ['Low', 'Medium', 'High'];

const TEAM = [
  { id: 'u1', name: 'Dana Whitfield', role: 'Product manager' },
  { id: 'u2', name: 'Marcus Reyes', role: 'Product designer' },
  { id: 'u3', name: 'Priya Nandakumar', role: 'Frontend engineer' },
  { id: 'u4', name: 'Tomas Lindqvist', role: 'Backend engineer' },
  { id: 'u5', name: 'Amara Okafor', role: 'QA analyst' },
  { id: 'u6', name: 'Ben Carter', role: 'Data analyst' }
];

const PROJECTS = [
  {
    id: 'p1', name: 'Website Redesign', owner: 'Dana Whitfield', status: 'Active', due: '2026-04-17',
    tasks: [
      { id: 'p1t1', title: 'Audit current page templates', status: 'Completed', assigneeId: 'u2', priority: 'Medium', due: '2026-01-16', blockedBy: null },
      { id: 'p1t2', title: 'Build shared component library', status: 'In progress', assigneeId: 'u3', priority: 'High', due: '2026-02-06', blockedBy: 'p1t1' },
      { id: 'p1t3', title: 'Homepage hero artwork', status: 'Not started', assigneeId: 'u2', priority: 'Medium', due: '2026-02-20', blockedBy: null },
      { id: 'p1t4', title: 'Accessibility pass on navigation', status: 'Blocked', assigneeId: 'u5', priority: 'High', due: '2026-03-02', blockedBy: 'p1t2' }
    ]
  },
  {
    id: 'p2', name: 'Mobile Checkout', owner: 'Priya Nandakumar', status: 'Active', due: '2026-05-08',
    tasks: [
      { id: 'p2t1', title: 'Rewrite payment form validation', status: 'In progress', assigneeId: 'u3', priority: 'High', due: '2026-01-30', blockedBy: null },
      { id: 'p2t2', title: 'Saved card selector', status: 'Not started', assigneeId: 'u4', priority: 'Medium', due: '2026-02-24', blockedBy: 'p2t1' },
      { id: 'p2t3', title: 'Checkout regression suite', status: 'Not started', assigneeId: 'u5', priority: 'High', due: '2026-03-20', blockedBy: 'p2t1' },
      { id: 'p2t4', title: 'Order confirmation email copy', status: 'Completed', assigneeId: 'u1', priority: 'Low', due: '2026-01-12', blockedBy: null }
    ]
  },
  {
    id: 'p3', name: 'Billing Migration', owner: 'Tomas Lindqvist', status: 'On hold', due: '2026-06-30',
    tasks: [
      { id: 'p3t1', title: 'Map legacy invoice fields', status: 'Completed', assigneeId: 'u4', priority: 'Medium', due: '2026-01-20', blockedBy: null },
      { id: 'p3t2', title: 'Dual-write reconciliation job', status: 'Blocked', assigneeId: 'u4', priority: 'High', due: '2026-03-11', blockedBy: 'p3t1' },
      { id: 'p3t3', title: 'Refund handling rules', status: 'Not started', assigneeId: 'u6', priority: 'Medium', due: '2026-04-02', blockedBy: 'p3t2' }
    ]
  },
  {
    id: 'p4', name: 'Customer Portal', owner: 'Dana Whitfield', status: 'Active', due: '2026-07-22',
    tasks: [
      { id: 'p4t1', title: 'Portal information architecture', status: 'Completed', assigneeId: 'u1', priority: 'Medium', due: '2026-02-03', blockedBy: null },
      { id: 'p4t2', title: 'Ticket history table', status: 'In review', assigneeId: 'u3', priority: 'High', due: '2026-03-27', blockedBy: null },
      { id: 'p4t3', title: 'Entitlement lookup service', status: 'In progress', assigneeId: 'u4', priority: 'High', due: '2026-04-10', blockedBy: null },
      { id: 'p4t4', title: 'Portal empty states', status: 'Not started', assigneeId: 'u2', priority: 'Low', due: '2026-05-15', blockedBy: 'p4t2' }
    ]
  },
  {
    id: 'p5', name: 'Analytics Dashboard', owner: 'Ben Carter', status: 'Completed', due: '2026-02-27',
    tasks: [
      { id: 'p5t1', title: 'Define north-star metric', status: 'Completed', assigneeId: 'u6', priority: 'High', due: '2026-01-09', blockedBy: null },
      { id: 'p5t2', title: 'Cohort retention chart', status: 'Completed', assigneeId: 'u6', priority: 'Medium', due: '2026-01-28', blockedBy: 'p5t1' },
      { id: 'p5t3', title: 'Export to spreadsheet', status: 'Completed', assigneeId: 'u3', priority: 'Low', due: '2026-02-13', blockedBy: null }
    ]
  },
  {
    id: 'p6', name: 'Support Chat Rollout', owner: 'Amara Okafor', status: 'Active', due: '2026-08-14',
    tasks: [
      { id: 'p6t1', title: 'Chat routing rules', status: 'In progress', assigneeId: 'u5', priority: 'High', due: '2026-04-24', blockedBy: null },
      { id: 'p6t2', title: 'Canned response library', status: 'Not started', assigneeId: 'u1', priority: 'Medium', due: '2026-05-19', blockedBy: null },
      { id: 'p6t3', title: 'Transcript retention policy', status: 'Not started', assigneeId: 'u6', priority: 'Low', due: '2026-06-09', blockedBy: 'p6t1' },
      { id: 'p6t4', title: 'Concurrent chat load check', status: 'Not started', assigneeId: 'u4', priority: 'High', due: '2026-06-26', blockedBy: 'p6t1' }
    ]
  },
  {
    id: 'p7', name: 'Data Retention Policy', owner: 'Ben Carter', status: 'On hold', due: '2026-09-04',
    tasks: [
      { id: 'p7t1', title: 'Inventory stored data classes', status: 'In review', assigneeId: 'u6', priority: 'High', due: '2026-05-05', blockedBy: null },
      { id: 'p7t2', title: 'Scheduled purge job', status: 'Not started', assigneeId: 'u4', priority: 'Medium', due: '2026-06-16', blockedBy: 'p7t1' }
    ]
  }
];

/* State */

const state = {
  busy: false,
  projectSeq: 0,
  taskSeq: 0,
  settings: { defaultProjectStatus: 'Active' },
  projects: { status: 'All', query: '' },
  tasks: { status: 'All', priority: 'All', query: '' },
  taskDialog: { mode: 'add', projectId: null, taskId: null },
  pendingDeleteId: null
};

/* Small helpers */

const $ = (sel) => document.querySelector(sel);

function esc(value) {
  return String(value == null ? '' : value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function todayISO() {
  const d = new Date();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return d.getFullYear() + '-' + m + '-' + day;
}

function findProject(id) {
  return state.projectsList.find((p) => p.id === id) || null;
}

function findTask(projectId, taskId) {
  const project = findProject(projectId);
  if (!project) return null;
  return project.tasks.find((t) => t.id === taskId) || null;
}

function memberName(id) {
  const member = TEAM.find((m) => m.id === id);
  return member ? member.name : 'Unassigned';
}

function taskProgress(project) {
  if (!project.tasks.length) return 0;
  const done = project.tasks.filter((t) => t.status === 'Completed').length;
  return Math.round((done / project.tasks.length) * 100);
}

function allTasks() {
  const rows = [];
  state.projectsList.forEach((project) => {
    project.tasks.forEach((task) => rows.push({ project, task }));
  });
  return rows;
}

/* Announce a message through the shared live region. */
function setStatus(message) {
  $('#status-region').textContent = message;
}

function showError(sel, message) {
  const node = $(sel);
  node.textContent = message;
  node.hidden = !message;
}

function clearError(sel) {
  showError(sel, '');
}

/* Simulated round trip: label the work, wait, then apply it. */
function runAsync(label, delay, control, work) {
  if (state.busy) return;
  state.busy = true;
  if (control) control.disabled = true;
  setStatus(label);
  window.setTimeout(() => {
    state.busy = false;
    if (control) control.disabled = false;
    work();
  }, delay);
}

/* Rendering */

function switchView(name) {
  ['projects', 'project', 'tasks', 'team', 'settings'].forEach((key) => {
    $('#view-' + key).hidden = key !== name;
  });
}

function markNav(route) {
  document.querySelectorAll('.sidenav a').forEach((link) => {
    const active = link.dataset.route === route;
    if (active) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  });
}

function renderProjects() {
  const filter = state.projects.status;
  const query = state.projects.query.trim().toLowerCase();
  const rows = state.projectsList.filter((project) => {
    const statusOk = filter === 'All' || project.status === filter;
    const haystack = (project.name + ' ' + project.owner).toLowerCase();
    const queryOk = !query || haystack.indexOf(query) !== -1;
    return statusOk && queryOk;
  });

  $('#projects-summary').textContent =
    rows.length + ' of ' + state.projectsList.length + ' projects shown';

  const body = $('#projects-body');
  body.innerHTML = rows.map((project) => {
    const progress = taskProgress(project);
    return '<tr>' +
      '<td><button type="button" class="link" data-open-project="' + esc(project.id) + '">' +
        esc(project.name) + '</button></td>' +
      '<td>' + esc(project.owner) + '</td>' +
      '<td><span class="pill">' + esc(project.status) + '</span></td>' +
      '<td>' + esc(project.due) + '</td>' +
      '<td>' + project.tasks.length + '</td>' +
      '<td><progress max="100" value="' + progress + '" aria-label="Progress for ' +
        esc(project.name) + '"></progress> ' + progress + '%</td>' +
      '<td><button type="button" class="btn small danger" data-delete-project="' +
        esc(project.id) + '" aria-label="Delete project: ' + esc(project.name) + '">Delete</button></td>' +
    '</tr>';
  }).join('');

  $('#projects-empty').hidden = rows.length > 0;
}

function renderProjectDetail() {
  const project = findProject(state.currentProjectId);
  if (!project) {
    go('#/projects');
    return;
  }

  $('#project-heading').textContent = project.name;

  const progress = taskProgress(project);
  const completed = project.tasks.filter((t) => t.status === 'Completed').length;
  $('#project-meta').innerHTML =
    '<div><dt>Owner</dt><dd>' + esc(project.owner) + '</dd></div>' +
    '<div><dt>Status</dt><dd>' + esc(project.status) + '</dd></div>' +
    '<div><dt>Due date</dt><dd>' + esc(project.due) + '</dd></div>' +
    '<div><dt>Tasks</dt><dd>' + project.tasks.length + '</dd></div>' +
    '<div><dt>Completed</dt><dd>' + completed + ' of ' + project.tasks.length + '</dd></div>' +
    '<div><dt>Progress</dt><dd>' + progress + '%</dd></div>';

  const body = $('#project-tasks-body');
  body.innerHTML = project.tasks.map((task) => {
    const blocker = task.blockedBy ? findTask(project.id, task.blockedBy) : null;
    const options = TASK_STATUSES.map((status) =>
      '<option value="' + esc(status) + '"' + (status === task.status ? ' selected' : '') + '>' +
      esc(status) + '</option>').join('');
    return '<tr>' +
      '<td>' + esc(task.title) + '</td>' +
      '<td><select class="row-select" data-task-status="' + esc(task.id) + '" aria-label="Status for ' +
        esc(task.title) + '">' + options + '</select></td>' +
      '<td>' + esc(memberName(task.assigneeId)) + '</td>' +
      '<td>' + esc(task.priority) + '</td>' +
      '<td>' + esc(task.due) + '</td>' +
      '<td>' + (blocker ? esc(blocker.title) : '&mdash;') + '</td>' +
      '<td><button type="button" class="btn small" data-edit-task="' + esc(task.id) +
        '" aria-label="Edit task: ' + esc(task.title) + '">Edit</button></td>' +
    '</tr>';
  }).join('');

  $('#project-tasks-empty').hidden = project.tasks.length > 0;
}

function renderTasks() {
  const filter = state.tasks;
  const query = filter.query.trim().toLowerCase();
  const rows = allTasks().filter(({ project, task }) => {
    const statusOk = filter.status === 'All' || task.status === filter.status;
    const priorityOk = filter.priority === 'All' || task.priority === filter.priority;
    const haystack = (task.title + ' ' + project.name + ' ' + memberName(task.assigneeId)).toLowerCase();
    const queryOk = !query || haystack.indexOf(query) !== -1;
    return statusOk && priorityOk && queryOk;
  });

  $('#tasks-summary').textContent = rows.length + ' tasks shown';

  $('#tasks-body').innerHTML = rows.map(({ project, task }) => {
    const blocker = task.blockedBy ? findTask(project.id, task.blockedBy) : null;
    return '<tr>' +
      '<td>' + esc(task.title) + '</td>' +
      '<td>' + esc(project.name) + '</td>' +
      '<td><span class="pill">' + esc(task.status) + '</span></td>' +
      '<td>' + esc(task.priority) + '</td>' +
      '<td>' + esc(memberName(task.assigneeId)) + '</td>' +
      '<td>' + esc(task.due) + '</td>' +
      '<td>' + (blocker ? esc(blocker.title) : '&mdash;') + '</td>' +
    '</tr>';
  }).join('');

  $('#tasks-empty').hidden = rows.length > 0;
}

function renderTeam() {
  const rows = TEAM.map((member) => {
    const assigned = allTasks().filter(({ task }) => task.assigneeId === member.id);
    const open = assigned.filter(({ task }) => task.status !== 'Completed').length;
    return '<tr>' +
      '<td>' + esc(member.name) + '</td>' +
      '<td>' + esc(member.role) + '</td>' +
      '<td>' + assigned.length + '</td>' +
      '<td>' + open + '</td>' +
    '</tr>';
  });
  $('#team-body').innerHTML = rows.join('');
}

function renderSettings() {
  $('#setting-default-status').value = state.settings.defaultProjectStatus;
}

/* Routing */
