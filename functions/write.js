// functions/write.js
export async function handler(event) {
  const APPS_SCRIPT_URL = 'https://script.google.com/macros/s/AKfycbzN3dtl3YEZKK1P7ndYr825xwfEqwVT_4ahjI_WcVFjXY6WRzcAgK4nBdeY_91vfoO2/exec';

  try {
    if (event.httpMethod !== 'POST') {
      return {
        statusCode: 405,
        body: JSON.stringify({ error: 'Method Not Allowed' })
      };
    }

    const body = JSON.parse(event.body || '{}');
    const comp = body.comp;
    const round = body.round;
    const data = body.data;

    if (!comp || !round || !Array.isArray(data)) {
      return {
        statusCode: 400,
        body: JSON.stringify({ error: 'Missing comp, round, or data' })
      };
    }

    const payload = {
      comp,
      round,
      data
    };

    const resp = await fetch(APPS_SCRIPT_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      redirect: 'follow'
    });

    const contentType = resp.headers.get('content-type') || '';
    const isJson = contentType.includes('application/json');

    const text = await resp.text();
    const parsed = isJson ? JSON.parse(text) : { raw: resp.status, body: text };

    return {
      statusCode: resp.status,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(parsed)
    };
  } catch (err) {
    return {
      statusCode: 500,
      body: JSON.stringify({ error: err.message })
    };
  }
}
