// netlify/functions/sheets-read.js
const { google } = require('googleapis');

exports.handler = async function(event) {
  try {
    const SA_KEY_RAW = process.env.GOOGLE_SERVICE_ACCOUNT_KEY;
    if (!SA_KEY_RAW) return { statusCode: 500, body: JSON.stringify({ error: 'Missing service account key' }) };

    // Accept either base64 or raw JSON
    let sa;
    try { sa = JSON.parse(Buffer.from(SA_KEY_RAW, 'base64').toString()); } catch (e) {
      try { sa = JSON.parse(SA_KEY_RAW); } catch (e2) { sa = null; }
    }
    if (!sa) return { statusCode: 500, body: JSON.stringify({ error: 'Invalid service account key' }) };

    const jwtClient = new google.auth.JWT(
      sa.client_email,
      null,
      sa.private_key,
      ['https://www.googleapis.com/auth/spreadsheets.readonly']
    );
    await jwtClient.authorize();
    const sheets = google.sheets({ version: 'v4', auth: jwtClient });

    const qs = event.queryStringParameters || {};
    const spreadsheetId = qs.spreadsheetId || process.env.GOOGLE_SHEETS_SPREADSHEET_ID;
    if (!spreadsheetId) return { statusCode: 400, body: JSON.stringify({ error: 'Missing spreadsheetId' }) };

    // If tab param provided, return values for that range
    if (qs.tab) {
      const range = qs.range || 'A2:Z500';
      const tab = qs.tab;
      const res = await sheets.spreadsheets.values.get({
        spreadsheetId,
        range: `${tab}!${range}`,
        valueRenderOption: 'UNFORMATTED_VALUE'
      });
      return { statusCode: 200, body: JSON.stringify({ values: res.data.values || [] }) };
    }

    // If comp & round provided, attempt to fetch a named range or sheet by name
    if (qs.comp && qs.round) {
      // client will pass encoded round name; decode
      const comp = decodeURIComponent(qs.comp);
      const round = decodeURIComponent(qs.round);
      // try to read sheet named exactly round
      try {
        const res = await sheets.spreadsheets.values.get({
          spreadsheetId,
          range: `${round}`,
          valueRenderOption: 'UNFORMATTED_VALUE'
        });
        // If returned values look like header+rows, normalize
        const values = res.data.values || [];
        // If first row is header, return headers/rows
        if (values.length >= 1) {
          const headers = values[0];
          const rows = values.slice(1);
          return { statusCode: 200, body: JSON.stringify({ headers, rows }) };
        }
        return { statusCode: 404, body: JSON.stringify({ error: 'No values' }) };
      } catch (err) {
        // fallback: try range with sheet!A1:Z999
        try {
          const res2 = await sheets.spreadsheets.values.get({
            spreadsheetId,
            range: `${round}!A1:Z999`,
            valueRenderOption: 'UNFORMATTED_VALUE'
          });
          const values = res2.data.values || [];
          if (values.length >= 1) {
            const headers = values[0];
            const rows = values.slice(1);
            return { statusCode: 200, body: JSON.stringify({ headers, rows }) };
          }
        } catch (e) {}
        return { statusCode: 404, body: JSON.stringify({ error: 'Round not found' }) };
      }
    }

    // Default: return a small meta snapshot (you can extend)
    return { statusCode: 400, body: JSON.stringify({ error: 'Missing parameters' }) };
  } catch (err) {
    return { statusCode: 500, body: JSON.stringify({ error: String(err && err.message || err) }) };
  }
};
