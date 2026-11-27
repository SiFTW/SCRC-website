/**
 * Sussex Cancer Research Centre — 1-Click Google Forms Generator
 *
 * Faithfully recreates all 7 Squarespace forms from sussexcancer.org as native
 * Google Forms inside a new "SCRC Website Forms" folder in your Google Drive,
 * links each form to its own Google Sheet for responses, and logs a ready-to-paste
 * JSON snippet for `src/data/site.json`.
 *
 * HOW TO RUN (takes ~10 seconds):
 *   1. Open https://script.new while signed into the Google account that should own the forms.
 *   2. Delete any placeholder code and paste this entire file.
 *   3. Click "Run" (createAllSCRCForms) and approve the Google Forms / Drive / Sheets permissions.
 *   4. Copy the printed `externalLinks` URLs from the Execution Log into `src/data/site.json`
 *      (or via Pages CMS -> Site Settings & Links).
 */

function createAllSCRCForms() {
  const folder = DriveApp.createFolder('SCRC Website Forms');
  const results = {};

  const formSpecs = [
    {
      key: 'joinFormUrl',
      title: 'SCRC — Get Involved / Join the Centre',
      description:
        'Join the Sussex Cancer Research Centre to receive our newsletter and updates, whether you are a researcher, clinician, patient, carer, or member of the public.',
      confirmation:
        'Thank you for joining the Sussex Cancer Research Centre! We will be in touch with our latest updates.',
      fields: [
        { type: 'text', title: 'First Name', required: true },
        { type: 'text', title: 'Last Name', required: true },
        { type: 'email', title: 'Email', required: true },
        {
          type: 'checkbox',
          title: 'Get more involved',
          help: 'Check one of these options if you want to get more involved.',
          options: [
            'I am a researcher who would like to join the centre',
            'I am a patient/carer/member of the public who would like to find ways to contribute to cancer research',
          ],
          required: false,
        },
        {
          type: 'paragraph',
          title:
            'We are looking to host events/workshops around common research interests in cancer research. Let us know 2-3 key words describing your interests:',
          required: false,
        },
      ],
    },
    {
      key: 'eventsFormUrl',
      title: 'SCRC — Event & Themed Meeting Sign-Up',
      description:
        'Sign up to attend Sussex Cancer Research Centre themed research meetings and events.',
      confirmation:
        'Thank you for signing up! We will send you the meeting invitations shortly.',
      fields: [
        { type: 'text', title: 'First Name', required: true },
        { type: 'text', title: 'Last Name', required: true },
        { type: 'email', title: 'Email', required: true },
        {
          type: 'checkbox',
          title: 'Which themes are you interested in attending?',
          options: ['Blood', 'Computational', 'Fatigue', 'Breast and gynae', 'Brain'],
          required: true,
        },
        {
          type: 'radio',
          title: 'Do you want an invite to all meetings or just one?',
          options: ['All meetings', 'Just one'],
          required: true,
        },
        {
          type: 'text',
          title: 'Anything else you want us to know?',
          required: false,
        },
      ],
    },
    {
      key: 'expertsFormUrl',
      title: 'SCRC — Experts by Experience Application',
      description:
        'Shape the future of cancer research across Sussex. No scientific knowledge is required to be an Expert by Experience.',
      confirmation:
        'Thank you for applying to become an SCRC Expert by Experience! A member of our team will be in touch soon.',
      fields: [
        { type: 'text', title: 'First Name', required: true },
        { type: 'text', title: 'Last Name', required: true },
        { type: 'email', title: 'Email', required: true },
        { type: 'text', title: 'Phone', required: false },
        {
          type: 'section',
          title: 'About you',
          help: 'We are aiming to build a diverse panel of lived experiences and backgrounds. Therefore we ask for a little information about yourself to ensure we can assemble a panel that represents all communities across Sussex.',
        },
        {
          type: 'text',
          title: 'Where do you live?',
          help: 'Please describe the city/town/region you live in.',
          required: true,
        },
        {
          type: 'checkbox',
          title: 'Source of experience',
          help: 'You may choose more than one',
          options: ['Patient', 'Carer', 'Family', 'Other'],
          required: true,
        },
        {
          type: 'text',
          title: 'What type of cancer have you experienced?',
          required: true,
        },
        {
          type: 'radio',
          title: 'Scientific knowledge',
          help: 'No scientific knowledge is required to be an Expert by Experience.',
          options: [
            'None',
            'Some interest in popular science',
            'Previous scientific experience e.g. through a science degree or job',
            'Scientific expert',
          ],
          required: true,
        },
        {
          type: 'paragraph',
          title: 'Personal Statement',
          help: 'In a few sentences please describe why you are interested in being involved as an "Expert by Experience" with the Sussex Cancer Research Centre. If you have had previous engagement with us please mention it here.',
          required: true,
        },
        {
          type: 'text',
          title: 'Availability',
          help: 'To help schedule meetings can you describe your approximate availability for meetings. You can attend either in-person or online.',
          required: false,
        },
      ],
    },
    {
      key: 'mentorFormUrl',
      title: 'SCRC — Join the Mentorship Scheme',
      description:
        'Join the cross-institutional Sussex Cancer Research Centre mentoring programme as a mentor or mentee.',
      confirmation:
        'Thank you for registering for the SCRC mentoring programme! We will be in touch to get the ball rolling.',
      fields: [
        { type: 'text', title: 'First Name', required: true },
        { type: 'text', title: 'Last Name', required: true },
        { type: 'email', title: 'Email', required: true },
        {
          type: 'radio',
          title: 'I would like to join as a',
          options: ['mentor', 'mentee'],
          required: true,
        },
        {
          type: 'paragraph',
          title: 'What do you most want to get out of the scheme?',
          help: 'Mentees: What would you most like your mentor to help you with?\n\nMentors: What areas are you most able to advise on?',
          required: false,
        },
      ],
    },
    {
      key: 'contactFormUrl',
      title: 'SCRC — Get in Touch',
      description:
        'Interested in working together? Want to know how you can help? Got a cool idea? Send a message to the Sussex Cancer Research Centre team.',
      confirmation: 'Thank you for getting in touch! We will respond as soon as possible.',
      fields: [
        { type: 'text', title: 'First Name', required: true },
        { type: 'text', title: 'Last Name', required: true },
        { type: 'email', title: 'Email', required: true },
        { type: 'text', title: 'Phone', required: false },
        { type: 'paragraph', title: 'Message', required: true },
      ],
    },
    {
      key: 'lymitFormUrl',
      title: 'SCRC — LyMIT 2026 Expression of Interest',
      description:
        'Register your interest in LyMIT 2026 — the latest research in lymphoid cancers using novel, multidisciplinary methods.',
      confirmation: 'Thank you for registering your interest in LyMIT 2026!',
      fields: [
        { type: 'text', title: 'First Name', required: true },
        { type: 'text', title: 'Last Name', required: true },
        { type: 'email', title: 'Email', required: true },
        { type: 'text', title: 'Institution', required: true },
        {
          type: 'checkbox',
          title: 'Interest in presenting?',
          options: ['Talk', 'Poster'],
          required: false,
        },
      ],
    },
    {
      key: 'posterAbstractFormUrl',
      title: 'SCRC — Annual Symposium Poster Abstract Submission',
      description: 'Submit a scientific and lay poster abstract for the SCRC Annual Symposium.',
      confirmation: 'Thank you! Your poster abstract has been submitted.',
      fields: [
        { type: 'text', title: 'Presenting author first name', required: true },
        { type: 'text', title: 'Presenting author last name', required: true },
        { type: 'email', title: 'Email', required: true },
        { type: 'text', title: 'Presenting author affiliation', required: false },
        { type: 'paragraph', title: 'Full author list', required: false },
        { type: 'text', title: 'Poster title', required: true },
        {
          type: 'paragraph',
          title: 'Scientific abstract',
          help: 'Provide a summary of your research and findings (300 words)',
          required: true,
        },
        {
          type: 'paragraph',
          title: 'Non-scientific abstract',
          help: 'Provide a summary of your work that is accessible to people outside research (300 words)',
          required: true,
        },
      ],
    },
  ];

  formSpecs.forEach((spec) => {
    const form = FormApp.create(spec.title);
    form.setDescription(spec.description);
    form.setConfirmationMessage(spec.confirmation);
    form.setAcceptingResponses(true);

    spec.fields.forEach((f) => {
      if (f.type === 'section') {
        const sec = form.addSectionHeaderItem().setTitle(f.title);
        if (f.help) sec.setHelpText(f.help);
      } else if (f.type === 'text') {
        const item = form.addTextItem().setTitle(f.title).setRequired(Boolean(f.required));
        if (f.help) item.setHelpText(f.help);
      } else if (f.type === 'email') {
        const emailValidation = FormApp.createTextValidation().requireTextIsEmail().build();
        const item = form
          .addTextItem()
          .setTitle(f.title)
          .setRequired(Boolean(f.required))
          .setValidation(emailValidation);
        if (f.help) item.setHelpText(f.help);
      } else if (f.type === 'paragraph') {
        const item = form
          .addParagraphTextItem()
          .setTitle(f.title)
          .setRequired(Boolean(f.required));
        if (f.help) item.setHelpText(f.help);
      } else if (f.type === 'checkbox') {
        const item = form
          .addCheckboxItem()
          .setTitle(f.title)
          .setChoiceValues(f.options)
          .setRequired(Boolean(f.required));
        if (f.help) item.setHelpText(f.help);
      } else if (f.type === 'radio') {
        const item = form
          .addMultipleChoiceItem()
          .setTitle(f.title)
          .setChoiceValues(f.options)
          .setRequired(Boolean(f.required));
        if (f.help) item.setHelpText(f.help);
      }
    });

    // Attach a Google Sheet to store responses neatly in the same folder
    const sheet = SpreadsheetApp.create(spec.title + ' (Responses)');
    form.setDestination(FormApp.DestinationType.SPREADSHEET, sheet.getId());

    // Move both Form and Response Sheet into the "SCRC Website Forms" folder
    DriveApp.getFileById(form.getId()).moveTo(folder);
    DriveApp.getFileById(sheet.getId()).moveTo(folder);

    results[spec.key] = form.getPublishedUrl();
    Logger.log('Created: ' + spec.title);
    Logger.log('  Public URL: ' + form.getPublishedUrl());
    Logger.log('  Edit URL:   ' + form.getEditUrl());
  });

  Logger.log('\n=== PASTE THESE INTO src/data/site.json ("externalLinks") ===');
  Logger.log(JSON.stringify(results, null, 2));
  Logger.log('\nGoogle Drive Folder: ' + folder.getUrl());
}
