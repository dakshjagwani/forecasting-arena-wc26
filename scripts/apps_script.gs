// Google Apps Script — deploy as a Web App
// Execute as: Me | Who has access: Anyone
//
// After deploying, copy the URL into site/picks.js → APPS_SCRIPT_URL
// and into .env / GitHub Secrets as APPS_SCRIPT_URL.

var SHEET_ID   = '';  // paste your Google Sheet ID here
var SHEET_NAME = 'picks';

function doPost(e) {
  try {
    var sheet = SpreadsheetApp.openById(SHEET_ID).getSheetByName(SHEET_NAME);
    var picks = JSON.parse(e.postData.contents);
    var now   = new Date().toISOString();

    picks.forEach(function(p) {
      sheet.appendRow([
        now,
        p.name    || '',
        p.slug    || '',
        p.match_id || '',
        p.p_home  || 0,
        p.p_draw  || 0,
        p.p_away  || 0,
      ]);
    });

    return ContentService
      .createTextOutput(JSON.stringify({ status: 'ok', n: picks.length }))
      .setMimeType(ContentService.MimeType.JSON);

  } catch (err) {
    return ContentService
      .createTextOutput(JSON.stringify({ status: 'error', message: err.message }))
      .setMimeType(ContentService.MimeType.JSON);
  }
}

// GET endpoint — health check; open in browser to confirm deployment is live
function doGet() {
  return ContentService
    .createTextOutput(JSON.stringify({ status: 'ok', service: 'Forecasting Arena picks endpoint' }))
    .setMimeType(ContentService.MimeType.JSON);
}
