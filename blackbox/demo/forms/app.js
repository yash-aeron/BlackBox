'use strict';

/* ------------------------------------------------------------------
   Application wizard. All state lives in the DOM plus a tiny bit of
   bookkeeping here; the visible step is encoded in the URL hash.
   ------------------------------------------------------------------ */

const STEP_NAMES = ['Applicant', 'Employment', 'Documents', 'Review'];
const TOTAL_STEPS = 4;

const state = {
  step: 1,
  /* number of consecutive steps validated so far; a step is reachable
     only when it is at most maxValidated + 1 */
  maxValidated: 0,
  fileName: '',
  reference: '',
  submitted: false,
  ready: false,
  refSeq: 0
};

const $ = (sel) => document.querySelector(sel);

function esc(value) {
  return String(value == null ? '' : value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function setStatus(message) {
  $('#status-region').textContent = message;
}

function showErrors(errors) {
  const box = $('#step-error');
  box.textContent = errors.map((e) => e.msg).join(' ');
  box.hidden = errors.length === 0;

  document.querySelectorAll('.invalid').forEach((el) => {
    el.classList.remove('invalid');
    el.removeAttribute('aria-invalid');
  });

  errors.forEach((e) => {
    if (!e.el) return;
    const field = $(e.el);
    if (!field) return;
    field.setAttribute('aria-invalid', 'true');
    field.classList.add('invalid');
  });
}

function clearErrors() {
  const box = $('#step-error');
  box.textContent = '';
  box.hidden = true;
  document.querySelectorAll('.invalid').forEach((el) => el.classList.remove('invalid'));
  document.querySelectorAll('[aria-invalid]').forEach((el) => el.removeAttribute('aria-invalid'));
}

/* ------------------------------------------------------------------
   Step validation
   ------------------------------------------------------------------ */

function needsIncome(status) {
  return status === 'Employed' || status === 'Self-employed';
}

function validateStep1() {
  const errors = [];
  const name = $('#full-name').value.trim();
  const email = $('#email').value.trim();
  const phone = $('#phone').value.trim();

  if (!name) errors.push({ el: '#full-name', msg: 'Full name is required.' });
  if (!email) errors.push({ el: '#email', msg: 'Email is required.' });
  else if (email.indexOf('@') === -1) errors.push({ el: '#email', msg: 'Email must contain an @ character.' });
  if (phone && !/^\d{10}$/.test(phone)) errors.push({ el: '#phone', msg: 'Phone must be exactly 10 digits.' });

  return errors;
}

function validateStep2() {
  const errors = [];
  const status = $('#employment-status').value;

  if (!status) errors.push({ el: '#employment-status', msg: 'Employment status is required.' });

  if (needsIncome(status)) {
    const raw = $('#annual-income').value.trim();
    if (raw === '') {
      errors.push({ el: '#annual-income', msg: 'Annual income is required for employed and self-employed applicants.' });
    } else if (Number.isNaN(Number(raw)) || Number(raw) < 0) {
      errors.push({ el: '#annual-income', msg: 'Annual income must be a number of zero or more.' });
    }
  }
  return errors;
}

function validateStep3() {
  const errors = [];
  const input = $('#id-document');

  if (!input.files || input.files.length === 0) {
    errors.push({ el: '#id-document', msg: 'Upload ID document is required.' });
  }
  if (!$('#confirm-genuine').checked) {
    errors.push({ el: '#confirm-genuine', msg: 'You must confirm the documents are genuine.' });
  }

  const ref = $('#reference-code').value.trim();
  if (ref && !/^[A-Za-z]{3}-\d{4}$/.test(ref)) {
    errors.push({ el: '#reference-code', msg: 'Reference code must use the format ABC-1234.' });
  }
  return errors;
}

function validateStep4() {
  const errors = [];
  if (!$('#agree-terms').checked) {
    errors.push({ el: '#agree-terms', msg: 'You must agree to the terms before submitting.' });
  }
  return errors;
}

function validateStep(step) {
  if (step === 1) return validateStep1();
  if (step === 2) return validateStep2();
  if (step === 3) return validateStep3();
  return validateStep4();
}

function firstStepsValid() {
  return validateStep1().length === 0 &&
         validateStep2().length === 0 &&
         validateStep3().length === 0;
}

/* ------------------------------------------------------------------
   Conditional fields (step 2)
   ------------------------------------------------------------------ */

function syncConditionalFields() {
  const status = $('#employment-status').value;
  const incomeField = $('#income-field');
  const employerField = $('#employer-field');

  const showIncome = needsIncome(status);
  const showEmployer = status === 'Employed';

  incomeField.hidden = !showIncome;
  employerField.hidden = !showEmployer;

  $('#annual-income').required = showIncome;
  /* Values typed while a field was visible are kept if it is shown again. */
}

/* ------------------------------------------------------------------
   Review summary (step 4)
   ------------------------------------------------------------------ */

function money(value) {
  return '$' + Number(value).toLocaleString('en-US');
}

function renderReview() {
  const status = $('#employment-status').value;
  const rows = [
    ['Full name', $('#full-name').value.trim() || 'Not provided'],
    ['Email', $('#email').value.trim() || 'Not provided'],
    ['Phone', $('#phone').value.trim() || 'Not provided'],
    ['Employment status', status || 'Not provided']
  ];

  if (needsIncome(status)) {
    const income = $('#annual-income').value.trim();
    rows.push(['Annual income', income === '' ? 'Not provided' : money(income)]);
  }
  if (status === 'Employed') {
    rows.push(['Employer name', $('#employer-name').value.trim() || 'Not provided']);
  }

  rows.push(['ID document', state.fileName || 'Not provided']);
  rows.push(['Documents confirmed', $('#confirm-genuine').checked ? 'Yes' : 'No']);
  rows.push(['Reference code', $('#reference-code').value.trim() || 'Not provided']);
  rows.push(['Terms agreed', $('#agree-terms').checked ? 'Yes' : 'No']);

  $('#review-list').innerHTML = rows.map((row) =>
    '<dt>' + esc(row[0]) + '</dt><dd>' + esc(row[1]) + '</dd>').join('');
}

/* ------------------------------------------------------------------
   Step rendering and navigation
   ------------------------------------------------------------------ */

function syncSubmit() {
  $('#submit-btn').disabled = !($('#agree-terms').checked && firstStepsValid());
}

function isReachable(step) {
  return step <= state.maxValidated + 1;
}

function renderStep(step) {
  state.step = step;

  for (let i = 1; i <= TOTAL_STEPS; i += 1) {
    $('#step-' + i).hidden = i !== step;
  }
  $('#confirmation').hidden = true;
  $('#wizard').hidden = false;

  $('#step-counter').textContent = 'Step ' + step + ' of ' + TOTAL_STEPS;
  document.querySelectorAll('.step-btn').forEach((btn) => {
    const n = Number(btn.dataset.step);
    const done = n <= state.maxValidated;
    btn.classList.toggle('done', done && n !== step);
    if (n === step) btn.setAttribute('aria-current', 'step');
    else btn.removeAttribute('aria-current');
  });

  $('#back-btn').disabled = step === 1;
  $('#next-btn').hidden = step === TOTAL_STEPS;
  $('#submit-btn').hidden = step !== TOTAL_STEPS;

  if (step === 2) syncConditionalFields();
  if (step === TOTAL_STEPS) renderReview();
  syncSubmit();

  if (state.ready) {
    const panel = $('#step-' + step);
    panel.setAttribute('tabindex', '-1');
    panel.focus();
  }
}

function goToStep(step) {
  const hash = '#/step-' + step;
  if (location.hash === hash) renderStep(step);
  else location.hash = hash;
}

function attemptNext() {
  const errors = validateStep(state.step);
  if (errors.length) {
    showErrors(errors);
    return;
  }
  clearErrors();
  state.maxValidated = Math.max(state.maxValidated, state.step);
  if (state.step < TOTAL_STEPS) goToStep(state.step + 1);
}

function route() {
  const raw = (location.hash || '').replace(/^#\/?/, '');

  if (raw === 'submitted') {
    if (state.submitted) {
      $('#wizard').hidden = true;
      $('#confirmation').hidden = false;
    } else {
      goToStep(1);
    }
    return;
  }

  const match = /^step-([1-4])$/.exec(raw);
  renderStep(match ? Number(match[1]) : 1);
}

/* ------------------------------------------------------------------
   File selection (File API only, nothing is uploaded)
   ------------------------------------------------------------------ */

function onFileChange() {
  const input = $('#id-document');
  const file = input.files && input.files[0];

  if (!file) {
    state.fileName = '';
    $('#file-status').textContent = '';
    syncSubmit();
    return;
  }

  setStatus('Reading file\u2026');
  const name = file.name;
  window.setTimeout(() => {
    state.fileName = name;
    $('#file-status').textContent = 'Selected file: ' + name;
    setStatus('File selected.');
    syncSubmit();
    if (state.step === TOTAL_STEPS) renderReview();
  }, 200);
}

/* ------------------------------------------------------------------
   Submit and confirmation
   ------------------------------------------------------------------ */

const STEP_OF_FIELD = {
  '#full-name': 1, '#email': 1, '#phone': 1,
  '#employment-status': 2, '#annual-income': 2,
  '#id-document': 3, '#confirm-genuine': 3, '#reference-code': 3,
  '#agree-terms': 4
};

function submitApplication(event) {
  event.preventDefault();

  if (state.step !== TOTAL_STEPS) {
    attemptNext();
    return;
  }

  const errors = validateStep1()
    .concat(validateStep2(), validateStep3(), validateStep4());

  if (errors.length) {
    showErrors(errors);
    const target = STEP_OF_FIELD[errors[0].el] || TOTAL_STEPS;
    if (target !== state.step) goToStep(target);
    return;
  }

  clearErrors();
  $('#submit-btn').disabled = true;
  setStatus('Submitting\u2026');

  window.setTimeout(() => {
    state.refSeq += 1;
    state.reference = 'GR-2026-' + String(state.refSeq).padStart(4, '0');
    state.submitted = true;
    state.maxValidated = TOTAL_STEPS;

    $('#reference-number').textContent = state.reference;
    $('#confirmation-applicant').textContent = $('#full-name').value.trim();
    $('#confirmation-date').textContent = new Date().toISOString().slice(0, 10);

    /* Update the DOM first, then the hash; the hashchange handler re-applies
       the same state, so the two never disagree. */
    $('#wizard').hidden = true;
    $('#confirmation').hidden = false;

    const hash = '#/submitted';
    if (location.hash !== hash) location.hash = hash;

    setStatus('Application submitted.');
    $('#confirmation-heading').focus();
  }, 350);
}

function startNewApplication() {
  setStatus('Resetting\u2026');
  window.setTimeout(() => {
    $('#wizard-form').reset();
    state.maxValidated = 0;
    state.fileName = '';
    state.reference = '';
    state.submitted = false;
    $('#file-status').textContent = '';
    clearErrors();
    syncConditionalFields();
    goToStep(1);
    setStatus('New application started.');
  }, 180);
}

/* ------------------------------------------------------------------
   Wire up
   ------------------------------------------------------------------ */

function onFieldActivity(event) {
  const target = event.target;
  if (target && target.hasAttribute && target.hasAttribute('aria-invalid')) {
    target.removeAttribute('aria-invalid');
    target.classList.remove('invalid');
  }
  if (target && target.id === 'employment-status') syncConditionalFields();
  syncSubmit();
  if (state.step === TOTAL_STEPS) renderReview();
}

function init() {
  $('#back-btn').addEventListener('click', () => {
    if (state.step > 1) {
      clearErrors();
      goToStep(state.step - 1);
    }
  });

  $('#next-btn').addEventListener('click', attemptNext);
  $('#wizard-form').addEventListener('submit', submitApplication);

  $('#step-list').addEventListener('click', (event) => {
    const btn = event.target.closest('[data-step]');
    if (!btn) return;
    const step = Number(btn.dataset.step);
    if (step === state.step) return;
    if (!isReachable(step)) {
      showErrors([{ msg: 'Complete the current step before moving to step ' + step + '.' }]);
      return;
    }
    clearErrors();
    goToStep(step);
  });

  $('#wizard-form').addEventListener('input', onFieldActivity);
  $('#wizard-form').addEventListener('change', onFieldActivity);
  $('#id-document').addEventListener('change', onFileChange);
  $('#start-new-btn').addEventListener('click', startNewApplication);

  window.addEventListener('hashchange', route);

  if (!location.hash || location.hash === '#' || location.hash === '#/') {
    try { history.replaceState(null, '', '#/step-1'); } catch (err) { /* ignore */ }
  }

  syncConditionalFields();
  route();
  syncSubmit();
  state.ready = true;
}

document.addEventListener('DOMContentLoaded', init);
