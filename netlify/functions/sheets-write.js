// netlify/functions/sheets-write.js
const { google } = require('googleapis');

exports.handler = async function(event) {
  try {
    if (event.httpMethod !== 'POST') return { statusCode: 405, body: 'Method Not Allowed' };
    const body = JSON.parse(event.body || '{}');
    const SA_KEY_RAW = process.env.GOOGLE_SERVICE_ACCOUNT_KEY;
    if (!SA_KEY_RAW) return { statusCode: 500, body: JSON.stringify({ error: 'Missing service account key' }) };

    let sa;
    try { sa = JSON.parse(Buffer.from(SA_KEY_RAW, 'base64').toString()); } catch (e) {
      try { sa = JSON.parse(SA_KEY_RAW); } catch (e2) { sa = null; }
    }
    if (!sa) return { statusCode: 500, body: JSON.stringify({ error: 'Invalid service account key' }) };

    const jwtClient = new google.auth.JWT(
      sa.client_email,
      null,
      sa.private_key,
      ['https://www.googleapis.com/auth/spreadsheets']
    );
    await jwtClient.authorize();
    const sheets = google.sheets({ version: 'v4', auth: jwtClient });

    const spreadsheetId = body.spreadsheetId || process.env.GOOGLE_SHEETS_SPREADSHEET_ID;
    const range = body.range;
    const values = body.values;
    if (!spreadsheetId || !range || !Array.isArray(values)) {
      return { statusCode: 400, body: JSON.stringify({ error: 'Missing range/values' }) };
    }

    const res = await sheets.spreadsheets.values.update({
      spreadsheetId,
      range,
      valueInputOption: 'USER_ENTERED',
      requestBody: { values }
    });

    return { statusCode: 200, body: JSON.stringify({ updated: res.data.updatedCells || 0 }) };
  } catch (err) {
    return { statusCode: 500, body: JSON.stringify({ error: String(err && err.message || err) }) };
  }
};
