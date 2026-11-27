/**
 * Sussex Cancer Research Centre — Automated Email Notifications for All 7 Google Forms
 *
 * Installs a single central trigger across all Google Forms inside your
 * "SCRC Website Forms" Google Drive folder. Whenever anyone submits any form,
 * an email is sent immediately to Beatrice Ramunno and Simon Mitchell containing:
 *   - Every submitted field and answer
 *   - Direct link to the linked Responses Google Sheet
 *   - Reply-To set to the submitter's email address (so you can hit Reply directly)
 *
 * HOW TO SET UP (takes ~15 seconds, one-time):
 *   1. Open https://script.new (or add a new file in the same Apps Script project you used).
 *   2. Paste this entire file and save the project (e.g. "SCRC Form Notifications").
 *   3. Select `installFormEmailTriggers` from the function dropdown and click "Run".
 *   4. Approve permissions (Google Forms, Drive, and Send Email).
 *   5. Check the Execution Log — it will confirm all 7 forms are connected!
 */

const RECIPIENTS = [
  'B.Ramunno@bsms.ac.uk',
  'S.A.Mitchell@bsms.ac.uk',
];

const FOLDER_NAME = 'SCRC Website Forms';

/**
 * Run this ONCE to attach email notification triggers to all forms in "SCRC Website Forms".
 * Safe to re-run: removes any previous triggers created by this script first so no duplicates occur.
 */
function installFormEmailTriggers() {
  // 1. Remove any existing triggers pointing to onSCRCFormSubmit to avoid duplicates
  ScriptApp.getProjectTriggers().forEach((trigger) => {
    if (trigger.getHandlerFunction() === 'onSCRCFormSubmit') {
      ScriptApp.deleteTrigger(trigger);
    }
  });

  // 2. Find the most recently created "SCRC Website Forms" folder
  const folders = DriveApp.getFoldersByName(FOLDER_NAME);
  let targetFolder = null;
  while (folders.hasNext()) {
    const candidate = folders.next();
    if (!targetFolder || candidate.getDateCreated() > targetFolder.getDateCreated()) {
      targetFolder = candidate;
    }
  }

  if (!targetFolder) {
    throw new Error(
      'Could not find a Google Drive folder named "' +
        FOLDER_NAME +
        '". Make sure you are signed into the same Google account that owns the forms.'
    );
  }

  // 3. Attach an onFormSubmit trigger to every Google Form in that folder
  const files = targetFolder.getFilesByType(MimeType.GOOGLE_FORMS);
  let count = 0;

  while (files.hasNext()) {
    const file = files.next();
    const form = FormApp.openById(file.getId());
    ScriptApp.newTrigger('onSCRCFormSubmit')
      .forForm(form)
      .onFormSubmit()
      .create();
    count++;
    Logger.log('Connected trigger (' + count + '): ' + form.getTitle());
  }

  Logger.log('\nDone! Email notifications are now active for ' + count + ' form(s).');
  Logger.log('Recipients: ' + RECIPIENTS.join(', '));
}

/**
 * Trigger handler — runs automatically whenever any connected SCRC Google Form is submitted.
 */
function onSCRCFormSubmit(e) {
  const form = e.source;
  const response = e.response;
  const formTitle = form.getTitle();
  const timestamp = Utilities.formatDate(
    response.getTimestamp(),
    'Europe/London',
    'dd MMM yyyy, HH:mm'
  );

  // Get linked Google Sheet URL (if linked)
  let sheetUrl = '';
  try {
    const destId = form.getDestinationId();
    if (destId) {
      sheetUrl = 'https://docs.google.com/spreadsheets/d/' + destId + '/edit';
    }
  } catch (err) {
    // Form has no spreadsheet destination linked
  }
  const formEditUrl = form.getEditUrl();

  // Extract all answers + detect submitter name & email for subject / Reply-To
  const itemResponses = response.getItemResponses();
  const rows = [];
  let submitterEmail = '';
  let firstName = '';
  let lastName = '';

  itemResponses.forEach((ir) => {
    const question = ir.getItem().getTitle();
    const rawAnswer = ir.getResponse();
    const answer = Array.isArray(rawAnswer) ? rawAnswer.join(', ') : String(rawAnswer || '');

    rows.push({ question, answer });

    const qLower = question.toLowerCase();
    if (!submitterEmail && qLower.includes('email') && answer.includes('@')) {
      submitterEmail = answer.trim();
    }
    if (!firstName && qLower.includes('first name')) {
      firstName = answer.trim();
    }
    if (!lastName && qLower.includes('last name')) {
      lastName = answer.trim();
    }
  });

  const submitterName = [firstName, lastName].filter(Boolean).join(' ');
  const subject =
    '[SCRC Form] ' +
    formTitle +
    (submitterName ? ' — ' + submitterName : '');

  // Plain-text fallback body
  const plainLines = [
    'New submission received for: ' + formTitle,
    'Submitted: ' + timestamp + ' (UK time)',
    '',
    '--- Form Contents ---',
  ];
  rows.forEach((r) => {
    plainLines.push(r.question + ':\n' + (r.answer || '(blank)') + '\n');
  });
  plainLines.push('--- Links ---');
  if (sheetUrl) {
    plainLines.push('Responses Spreadsheet: ' + sheetUrl);
  }
  plainLines.push('Edit Form: ' + formEditUrl);

  // Clean HTML email body matching SCRC styling
  const tableRowsHtml = rows
    .map(
      (r) =>
        '<tr>' +
        '<td style="padding:10px 14px;border-bottom:1px solid #e5ece9;font-weight:600;color:#1e423d;vertical-align:top;width:36%;">' +
        escapeHtml_(r.question) +
        '</td>' +
        '<td style="padding:10px 14px;border-bottom:1px solid #e5ece9;color:#111816;white-space:pre-wrap;">' +
        escapeHtml_(r.answer || '—') +
        '</td>' +
        '</tr>'
    )
    .join('');

  const htmlBody =
    '<div style="font-family:Arial,Helvetica,sans-serif;max-width:680px;margin:0 auto;color:#111816;">' +
    '<div style="background:#8cd9bc;padding:18px 24px;border-radius:10px 10px 0 0;">' +
    '<div style="font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:0.06em;color:#1e423d;">Sussex Cancer Research Centre</div>' +
    '<h2 style="margin:4px 0 0;font-size:20px;color:#000000;">' +
    escapeHtml_(formTitle) +
    '</h2>' +
    '<div style="font-size:13px;color:#1e423d;margin-top:4px;">Submitted ' +
    escapeHtml_(timestamp) +
    ' (UK time)</div>' +
    '</div>' +
    '<div style="border:1px solid #d9e5e0;border-top:none;border-radius:0 0 10px 10px;padding:20px 24px;background:#ffffff;">' +
    '<table style="width:100%;border-collapse:collapse;font-size:14px;line-height:1.5;">' +
    tableRowsHtml +
    '</table>' +
    '<div style="margin-top:22px;padding-top:16px;border-top:1px solid #e5ece9;">' +
    (sheetUrl
      ? '<a href="' +
        sheetUrl +
        '" style="display:inline-block;padding:10px 18px;background:#000000;color:#ffffff;text-decoration:none;border-radius:8px;font-size:14px;font-weight:600;margin-right:10px;">Open Responses Spreadsheet &rarr;</a>'
      : '') +
    '<a href="' +
    formEditUrl +
    '" style="display:inline-block;padding:10px 16px;background:#eef8f4;color:#1e423d;text-decoration:none;border-radius:8px;font-size:14px;font-weight:600;">View Form</a>' +
    '</div>' +
    '</div>' +
    '</div>';

  const mailOptions = {
    htmlBody: htmlBody,
    name: 'Sussex Cancer Research Centre Website',
  };
  if (submitterEmail) {
    mailOptions.replyTo = submitterEmail;
  }

  MailApp.sendEmail(
    RECIPIENTS.join(','),
    subject,
    plainLines.join('\n'),
    mailOptions
  );
}

function escapeHtml_(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
